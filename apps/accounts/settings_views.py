"""Workspace settings: Profile & security, Users & roles. Every change is audited."""
import io

import pyotp
import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import audit
from apps.core.permissions import require_perm

from .forms import CodeForm, InviteForm, PasswordChangeForm
from .mail import send_invite
from .models import Invitation, Membership
from .roles import ROLE_CHOICES, ROLES

# ---------- Profile & security ----------


@login_required
def profile(request):
    needs_totp = request.membership.role in ("owner", "admin") and not request.user.totp_enabled
    from apps.notifications.models import NotificationPreference

    return render(request, "settings/profile.html", {"pw_form": PasswordChangeForm(request.user), "needs_totp": needs_totp, "section": "profile", "prefs": NotificationPreference.for_user(request.user)})


@login_required
@require_POST
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST)
    if form.is_valid():
        request.user.set_password(form.cleaned_data["new"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        audit(request, "auth.password_change", request.user)
        messages.success(request, "Password changed.")
        return redirect("accounts:profile")
    return render(request, "settings/profile.html", {"pw_form": form, "section": "profile"}, status=400)


@login_required
def totp_setup(request):
    """Step 1 shows a QR and secret (held in the session until confirmed); step 2 verifies a code and enables."""
    if request.method == "POST":
        form = CodeForm(request.POST)
        secret = request.session.get("totp_pending")
        if form.is_valid() and secret and pyotp.TOTP(secret).verify(form.cleaned_data["code"].replace(" ", ""), valid_window=1):
            request.user.set_totp_secret(secret)
            request.user.totp_enabled = True
            request.user.save(update_fields=["totp_secret_enc", "totp_enabled"])
            request.session.pop("totp_pending", None)
            audit(request, "auth.totp_enable", request.user)
            messages.success(request, "Two-step verification is on.")
            return redirect("accounts:profile")
        form.add_error("code", "That code didn't match. Scan again and enter the current code.")
    else:
        form = CodeForm()
        request.session["totp_pending"] = pyotp.random_base32()
    secret = request.session["totp_pending"]
    uri = pyotp.TOTP(secret).provisioning_uri(name=request.user.email, issuer_name="QuantumLoop")
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return render(request, "settings/totp_setup.html", {"form": form, "secret": secret, "qr_svg": buf.getvalue().decode(), "section": "profile"})


@login_required
@require_POST
def totp_disable(request):
    if not request.user.check_password(request.POST.get("password", "")):
        messages.error(request, "Enter your current password to turn off two-step verification.")
        return redirect("accounts:profile")
    request.user.totp_enabled = False
    request.user.totp_secret_enc = ""
    request.user.save(update_fields=["totp_enabled", "totp_secret_enc"])
    audit(request, "auth.totp_disable", request.user)
    messages.success(request, "Two-step verification is off.")
    return redirect("accounts:profile")


# ---------- Users & roles ----------


def _members(request):
    return Membership.objects.filter(tenant=request.tenant).select_related("user", "manager__user").annotate(n_reports=Count("reports")).order_by("-is_active", "user__name")


def _open_invites(request):
    return Invitation.objects.filter(tenant=request.tenant, accepted_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now())


def _assignable_roles(request):
    """Only an Owner may grant or change the Owner role."""
    return [c for c in ROLE_CHOICES if c[0] != "owner" or request.membership.role == "owner"]


@login_required
@require_perm("users.manage")
def users(request):
    members = list(_members(request))
    active = [m for m in members if m.is_active]
    ctx = {
        "members": members,
        "active_count": len(active),
        "limit": request.tenant.user_limit,
        "invites": _open_invites(request).select_related("invited_by").order_by("-created_at"),
        "roles": [(code, r["name"], sorted(r["perms"].items())) for code, r in ROLES.items()],
        "assignable": _assignable_roles(request),
        "managers": [m for m in active if m.role in ("owner", "admin", "manager")],
        "invite_form": InviteForm(),
        "section": "users",
    }
    return render(request, "settings/users.html", ctx)


@login_required
@require_perm("users.manage")
@require_POST
def invite(request):
    form = InviteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("accounts:users")
    active = _members(request).filter(is_active=True).count()
    if active + _open_invites(request).count() >= request.tenant.user_limit:
        messages.error(request, f"Your plan includes {request.tenant.user_limit} users. Upgrade or deactivate someone first.")
        return redirect("accounts:users")
    email, role = form.cleaned_data["email"], form.cleaned_data["role"]
    if role == "owner" and request.membership.role != "owner":
        raise PermissionDenied("Only an Owner can invite another Owner.")
    if Membership.objects.filter(tenant=request.tenant, user__email=email, is_active=True).exists():
        messages.error(request, f"{email} is already a member.")
        return redirect("accounts:users")
    manager = Membership.objects.filter(tenant=request.tenant, pk=form.cleaned_data.get("manager")).first() if form.cleaned_data.get("manager") else None
    inv, token = Invitation.create(tenant=request.tenant, email=email, role=role, invited_by=request.user, manager=manager)
    send_invite(inv, token)
    audit(request, "invite.send", inv, after={"email": email, "role": role})
    messages.success(request, f"Invitation sent to {email}.")
    return redirect("accounts:users")


@login_required
@require_perm("users.manage")
@require_POST
def resend_invite(request, pk):
    inv = get_object_or_404(Invitation, pk=pk, tenant=request.tenant)
    new, token = Invitation.create(tenant=request.tenant, email=inv.email, role=inv.role, invited_by=request.user, manager=inv.manager)
    send_invite(new, token)
    audit(request, "invite.resend", new, after={"email": new.email})
    messages.success(request, f"Invitation re-sent to {new.email}.")
    return redirect("accounts:users")


@login_required
@require_perm("users.manage")
@require_POST
def revoke_invite(request, pk):
    inv = get_object_or_404(Invitation, pk=pk, tenant=request.tenant)
    inv.revoked_at = timezone.now()
    inv.save(update_fields=["revoked_at"])
    audit(request, "invite.revoke", inv, after={"email": inv.email})
    messages.success(request, f"Invitation to {inv.email} revoked.")
    return redirect("accounts:users")


def _guard_owner_changes(request, m: Membership, new_role, deactivate: bool):
    if m.role == "owner" and request.membership.role != "owner":
        raise PermissionDenied("Only an Owner can change another Owner.")
    if new_role == "owner" and request.membership.role != "owner":
        raise PermissionDenied("Only an Owner can grant the Owner role.")
    losing_owner = m.role == "owner" and (deactivate or (new_role and new_role != "owner"))
    if losing_owner and not Membership.objects.filter(tenant=request.tenant, role="owner", is_active=True).exclude(pk=m.pk).exists():
        raise PermissionDenied("A workspace needs at least one active Owner.")


@login_required
@require_perm("users.manage")
@require_POST
def change_role(request, pk):
    m = get_object_or_404(Membership, pk=pk, tenant=request.tenant)
    new_role = request.POST.get("role")
    manager_id = request.POST.get("manager") or None
    if new_role not in ROLES:
        return HttpResponse("Unknown role", status=400)
    if m.user_id == request.user.id and new_role != m.role:
        messages.error(request, "You cannot change your own role. Ask another Owner or Admin.")
        return redirect("accounts:users")
    _guard_owner_changes(request, m, new_role, False)
    before = {"role": m.role, "manager": m.manager_id}
    m.role = new_role
    m.manager = Membership.objects.filter(tenant=request.tenant, pk=manager_id).first() if manager_id else None
    if m.manager_id == m.id:
        m.manager = None
    m.save(update_fields=["role", "manager"])
    audit(request, "role.change", m, before=before, after={"role": m.role, "manager": m.manager_id})
    messages.success(request, f"{m.user.name} is now {m.role_name}.")
    return redirect("accounts:users")


@login_required
@require_perm("users.manage")
@require_POST
def toggle_active(request, pk):
    m = get_object_or_404(Membership, pk=pk, tenant=request.tenant)
    if m.user_id == request.user.id:
        messages.error(request, "You cannot deactivate yourself.")
        return redirect("accounts:users")
    if m.is_active:
        _guard_owner_changes(request, m, None, True)
    elif _members(request).filter(is_active=True).count() >= request.tenant.user_limit:
        messages.error(request, f"Your plan includes {request.tenant.user_limit} users. Upgrade or deactivate someone first.")
        return redirect("accounts:users")
    m.is_active = not m.is_active
    m.save(update_fields=["is_active"])
    audit(request, "user.reactivate" if m.is_active else "user.deactivate", m)
    messages.success(request, f"{m.user.name} {'reactivated' if m.is_active else 'deactivated'}.")
    return redirect("accounts:users")
