import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import Membership
from apps.core.audit import audit
from apps.core.permissions import has_perm, require_perm
from apps.crm.models import Company, Contact

from . import services
from .forms import ContractForm, LapseForm, ProductForm, RenewForm
from .models import Contract, Product


def _refresh():
    r = HttpResponse(status=204)
    r["HX-Refresh"] = "true"
    return r


def _err(form):
    return HttpResponse(f'<div class="field-err" role="alert">{"; ".join(e[0] for e in form.errors.values())}</div>', status=422)


def _owners(request):
    scope = request.membership.scope_for("contracts.create") or request.membership.scope_for("contracts.edit") or "own"
    qs = Membership.objects.filter(tenant=request.tenant, is_active=True).select_related("user").order_by("user__name")
    if scope == "team":
        qs = qs.filter(user_id__in=request.membership.team_user_ids())
    elif scope == "own":
        qs = qs.filter(user=request.user)
    return [m.user for m in qs]


def _get(request, pk) -> Contract:
    return get_object_or_404(services.scoped_contracts(request).select_related("contact", "renewal_of", "decided_by", "lead"), pk=pk)


# ---------- contracts ----------

@login_required
@require_perm("contracts.view")
def index(request):
    today = timezone.localdate()
    status = request.GET.get("status", "active")
    bucket = request.GET.get("due", "")
    owner = request.GET.get("owner", "")
    q = (request.GET.get("q") or "").strip()
    base = services.scoped_contracts(request)
    qs = base
    if status != "all":
        qs = qs.filter(status=status)
    if bucket == "expired":
        qs = qs.filter(end_date__lt=today)
    elif bucket.isdigit():
        qs = qs.filter(end_date__gte=today, end_date__lte=today + timedelta(days=int(bucket)))
    if owner.isdigit():
        qs = qs.filter(owner_id=int(owner))
    if q:
        qs = qs.filter(Q(company__name__icontains=q) | Q(product__name__icontains=q) | Q(title__icontains=q) | Q(number__icontains=q))
    order = "end_date" if status == "active" else "-updated_at"
    page = Paginator(qs.order_by(order, "-id"), 25).get_page(request.GET.get("page"))
    counts = dict(base.values_list("status").annotate(n=Count("id")))
    ctx = {"page": page, "status": status, "due": bucket, "owner": owner, "q": q, "counts": counts, "total": sum(counts.values()), "owners": _owners(request), "today": today,
           "book": services.active_book(base), "risk": services.at_risk_summary(base, today), "can_create": has_perm(request, "contracts.create"), "statuses": Contract.Status.choices}
    return render(request, "contracts/index.html", ctx)


@login_required
@require_perm("contracts.view")
def renewals(request):
    today = timezone.localdate()
    owner = request.GET.get("owner", "")
    base = services.scoped_contracts(request)
    if owner.isdigit():
        base = base.filter(owner_id=int(owner))
    board = services.renewal_board(base, today)
    lapsed = list(base.filter(status=Contract.Status.LAPSED, decided_at__gte=timezone.now() - timedelta(days=180)).order_by("-decided_at")[:20])
    coming = list(base.filter(status=Contract.Status.ACTIVE, end_date__gt=today + timedelta(days=90), end_date__lte=today + timedelta(days=180)).order_by("end_date")[:10])
    renewed = base.filter(status=Contract.Status.RENEWED, decided_at__gte=timezone.now() - timedelta(days=90)).count()
    lapsed_n = base.filter(status=Contract.Status.LAPSED, decided_at__gte=timezone.now() - timedelta(days=90)).count()
    rate = round(renewed * 100 / (renewed + lapsed_n)) if renewed + lapsed_n else None
    ctx = {"board": board, "lapsed": lapsed, "coming": coming, "owner": owner, "owners": _owners(request), "today": today, "renewed_90": renewed, "lapsed_90": lapsed_n, "rate": rate,
           "order": [("expired", "Expired · decide now", "red"), ("7", "Next 7 days", "red"), ("30", "8 to 30 days", "yellow"), ("60", "31 to 60 days", ""), ("90", "61 to 90 days", "")]}
    return render(request, "contracts/renewals.html", ctx)


@login_required
@require_perm("contracts.view")
def detail(request, pk):
    c = _get(request, pk)
    chain = []
    cur = c.renewal_of
    while cur and len(chain) < 10:
        chain.append(cur)
        cur = cur.renewal_of
    successor = c.renewed_by
    tasks = list(c.tasks.select_related("assignee").order_by("status", "due_date")[:20]) if hasattr(c, "tasks") else []
    others = Contract.objects.filter(company=c.company).exclude(pk=c.pk).exclude(pk__in=[x.pk for x in chain]).select_related("product")[:10]
    ctx = {"c": c, "chain": chain, "successor": successor, "tasks": tasks, "others": others, "can_edit": services.can_edit(request, c), "can_task": has_perm(request, "tasks.create"), "today": timezone.localdate()}
    return render(request, "contracts/detail.html", ctx)


@login_required
def form(request, pk=None):
    c = _get(request, pk) if pk else None
    if c is None and not has_perm(request, "contracts.create"):
        raise PermissionDenied("You cannot create contracts.")
    if c is not None and not services.can_edit(request, c):
        raise PermissionDenied("You cannot edit this contract.")
    users = _owners(request)
    initial = {}
    if c is None:
        today = timezone.localdate()
        initial = {"owner": request.user.pk, "start_date": today, "end_date": today.replace(year=today.year + 1) - timedelta(days=1), "billing_cycle": "yearly"}
        for k in ("company", "lead"):
            if request.GET.get(k, "").isdigit():
                initial[k] = int(request.GET[k])
        if "lead" in initial:
            from apps.crm.models import Lead

            lead = Lead.objects.filter(pk=initial["lead"]).select_related("company", "owner").first()
            if lead:
                initial.update({"company": lead.company_id, "owner": lead.owner_id or request.user.pk, "amount": (lead.est_value_paise or 0) / 100 or None, "contact_email": lead.email})
    form_ = ContractForm(request.POST or None, instance=c, users=users, initial=initial or None)
    if request.method == "POST" and form_.is_valid():
        before = {"amount": c.amount_paise, "end": str(c.end_date), "owner": c.owner_id, "status": c.status} if c else None
        obj = form_.save(commit=False)
        if c is None:
            obj.created_by = request.user
        obj.save()
        audit(request, "contract.edit" if c else "contract.create", obj, before=before, after={"amount": obj.amount_paise, "end": str(obj.end_date), "owner": obj.owner_id, "company": obj.company_id, "product": obj.product_id})
        messages.success(request, f"Contract {obj.number} {'updated' if c else 'created'}.")
        return redirect("contracts:detail", pk=obj.pk)
    products = list(Product.objects.filter(is_active=True))
    return render(request, "contracts/form.html", {"form": form_, "c": c, "is_new": c is None, "products_json": json.dumps({str(p.pk): {"price": p.default_price_paise / 100, "cycle": p.default_cycle} for p in products})})


@login_required
@require_GET
def contacts_for(request, company_id):
    """HTMX: contact <select> options for the chosen company."""
    get_object_or_404(Company, pk=company_id)
    opts = "".join(f'<option value="{x.pk}" data-email="{x.email}">{x.name}{" · " + x.designation if x.designation else ""}</option>' for x in Contact.objects.filter(company_id=company_id).order_by("-is_primary", "name"))
    return HttpResponse('<option value="">— none —</option>' + opts)


@login_required
def renew(request, pk):
    c = _get(request, pk)
    if not services.can_edit(request, c):
        raise PermissionDenied("You cannot renew this contract.")
    if c.status not in (Contract.Status.ACTIVE, Contract.Status.LAPSED):
        raise PermissionDenied("Only active or lapsed contracts can be renewed.")
    form_ = RenewForm(request.POST or None, contract=c)
    if request.method == "POST":
        if not form_.is_valid():
            return _err(form_)
        d = form_.cleaned_data
        new = services.renew(c, request.user, d["start_date"], d["end_date"], form_.fields["amount"].to_paise(d["amount"]), d["auto_renew"], d.get("notes", ""))
        audit(request, "contract.renew", c, after={"new": new.number, "amount": new.amount_paise, "end": str(new.end_date), "won_back": c.status == Contract.Status.LAPSED})
        messages.success(request, f"Renewed as {new.number}. Raise the invoice when ready.")
        r = HttpResponse(status=204)
        r["HX-Redirect"] = f"/contracts/{new.pk}/"
        return r
    return render(request, "contracts/_renew_drawer.html", {"c": c, "form": form_})


@login_required
def lapse(request, pk):
    c = _get(request, pk)
    if not services.can_edit(request, c) or c.status != Contract.Status.ACTIVE:
        raise PermissionDenied("Only an active contract you own can be marked lapsed.")
    form_ = LapseForm(request.POST or None)
    if request.method == "POST":
        if not form_.is_valid():
            return _err(form_)
        services.lapse(c, request.user, form_.cleaned_data["reason"])
        audit(request, "contract.lapse", c, after={"reason": c.lapsed_reason})
        messages.success(request, f"{c.company.name} marked lapsed. It stays on the tracker for win-back.")
        return _refresh()
    return render(request, "contracts/_lapse_drawer.html", {"c": c, "form": form_})


@login_required
@require_POST
def cancel(request, pk):
    c = _get(request, pk)
    if not services.can_edit(request, c) or c.status != Contract.Status.ACTIVE:
        raise PermissionDenied("Only an active contract you own can be cancelled.")
    c.status = Contract.Status.CANCELLED
    c.decided_at, c.decided_by = timezone.now(), request.user
    c.lapsed_reason = (request.POST.get("reason") or "").strip()[:255]
    c.save(update_fields=["status", "decided_at", "decided_by", "lapsed_reason", "updated_at"])
    audit(request, "contract.cancel", c, after={"reason": c.lapsed_reason})
    messages.success(request, "Contract cancelled.")
    return redirect("contracts:detail", pk=c.pk)


# ---------- catalogue ----------

@login_required
@require_perm("contracts.view")
def products(request):
    qs = Product.objects.annotate(n_active=Count("contracts", filter=Q(contracts__status="active")), n_all=Count("contracts"))
    show = request.GET.get("show", "active")
    if show == "active":
        qs = qs.filter(is_active=True)
    return render(request, "contracts/products.html", {"rows": qs, "show": show, "can_manage": services.can_manage_catalogue(request)})


@login_required
def product_form(request, pk=None):
    if not services.can_manage_catalogue(request):
        raise PermissionDenied("Managers, accountants and admins maintain the catalogue.")
    p = get_object_or_404(Product, pk=pk) if pk else None
    form_ = ProductForm(request.POST or None, instance=p)
    if request.method == "POST":
        if not form_.is_valid():
            return _err(form_)
        obj = form_.save(commit=False)
        if p is None:
            obj.created_by = request.user
        obj.save()
        audit(request, "product.edit" if p else "product.create", obj, after={"name": obj.name, "price": obj.default_price_paise, "gst": str(obj.gst_rate), "hsn_sac": obj.hsn_sac})
        return _refresh()
    return render(request, "contracts/_product_drawer.html", {"form": form_, "p": p})
