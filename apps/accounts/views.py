"""
Sign-in: email + password, or a one-time code by email. Users who have enabled
TOTP get a second step. Login and code requests are rate-limited (section 9).
"""
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from apps.core import throttle
from apps.core.audit import audit

from .forms import AcceptInviteForm, CodeForm, EmailForm, LoginForm
from .mail import send_otp
from .models import Invitation, OneTimeCode

User = get_user_model()
LOGIN_LIMIT, LOGIN_WINDOW = 10, 15 * 60
OTP_REQ_LIMIT, OTP_REQ_WINDOW = 5, 60 * 60


def _safe_next(request, value=None):
    nxt = value if value is not None else (request.POST.get("next") or request.GET.get("next") or "")
    return nxt if url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) else reverse("dashboard:home")


def _finish_login(request, user, method: str, nxt: str):
    """Second factor if the user has TOTP, else sign in."""
    if user.totp_enabled:
        request.session["2fa_user_id"] = user.pk
        request.session["2fa_next"] = nxt
        request.session["2fa_method"] = method
        return redirect("accounts:twofa")
    login(request, user)
    audit(request, "auth.login", user, note=method)
    return redirect(nxt)


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        key = throttle.client_key(request, "login", request.POST.get("username", ""))
        if throttle.hit(key, LOGIN_LIMIT, LOGIN_WINDOW):
            form = self.get_form()
            form.add_error(None, "Too many attempts. Try again in 15 minutes or sign in with a code.")
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        throttle.reset(throttle.client_key(self.request, "login", form.cleaned_data["username"]))
        return _finish_login(self.request, form.get_user(), "password", _safe_next(self.request))


@require_http_methods(["GET", "POST"])
def otp_request(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if throttle.hit(throttle.client_key(request, "otp-req", email), OTP_REQ_LIMIT, OTP_REQ_WINDOW):
            form.add_error(None, "Too many codes requested. Try again in an hour.")
        else:
            # Same response whether or not the account exists, so emails can't be enumerated.
            if User.objects.filter(email=email, is_active=True).exists():
                _, code = OneTimeCode.issue(email)
                send_otp(email, code)
            request.session["otp_email"] = email
            request.session["otp_next"] = _safe_next(request)
            return redirect("accounts:otp_verify")
    return render(request, "accounts/otp_request.html", {"form": form, "next": request.GET.get("next", "")})


@require_http_methods(["GET", "POST"])
def otp_verify(request):
    email = request.session.get("otp_email")
    if not email:
        return redirect("accounts:otp")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if OneTimeCode.consume(email, form.cleaned_data["code"]):
            user = User.objects.filter(email=email, is_active=True).first()
            if user:
                request.session.pop("otp_email", None)
                nxt = _safe_next(request, request.session.pop("otp_next", ""))
                return _finish_login(request, user, "otp", nxt)
        form.add_error("code", "That code is wrong or has expired.")
    return render(request, "accounts/otp_verify.html", {"form": form, "email": email})


@require_http_methods(["GET", "POST"])
def twofa(request):
    uid = request.session.get("2fa_user_id")
    if not uid:
        return redirect("accounts:login")
    user = User.objects.filter(pk=uid, is_active=True).first()
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if throttle.hit(throttle.client_key(request, "2fa", uid), LOGIN_LIMIT, LOGIN_WINDOW):
            form.add_error(None, "Too many attempts. Try again in 15 minutes.")
        elif user and user.verify_totp(form.cleaned_data["code"]):
            nxt = _safe_next(request, request.session.pop("2fa_next", ""))
            method = request.session.pop("2fa_method", "password")
            request.session.pop("2fa_user_id", None)
            login(request, user)
            audit(request, "auth.login", user, note=f"{method}+totp")
            return redirect(nxt)
        else:
            form.add_error("code", "That code is wrong.")
    return render(request, "accounts/twofa.html", {"form": form, "user_obj": user})


@require_http_methods(["GET", "POST"])
def accept_invite(request, token):
    inv = Invitation.by_token(token)
    if inv is None or not inv.is_open:
        return render(request, "accounts/invite_invalid.html", status=410)
    existing = User.objects.filter(email=inv.email).first()
    form = AcceptInviteForm(request.POST or None, needs_password=existing is None, initial={"name": existing.name if existing else ""})
    if request.method == "POST" and form.is_valid():
        m = inv.accept(name=form.cleaned_data["name"], password=form.cleaned_data["password"] or None)
        login(request, m.user)
        audit(request, "invite.accept", m, after={"role": m.role, "tenant": inv.tenant_id})
        messages.success(request, f"Welcome to {inv.tenant.name}.")
        return redirect("dashboard:home")
    return render(request, "accounts/invite_accept.html", {"inv": inv, "form": form, "existing": existing})
