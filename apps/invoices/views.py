import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Membership
from apps.contracts.models import Contract, Product
from apps.core.audit import audit
from apps.core.permissions import has_perm, require_perm
from apps.crm.models import Company

from . import services
from .forms import InvoiceHeadForm, InvoiceSettingsForm, PaymentForm, parse_lines
from .gst import STATE_CODES, STATE_CHOICES
from .models import Invoice, InvoiceLine, InvoiceSettings, Payment
from .pdf import build_invoice_pdf


def _refresh():
    r = HttpResponse(status=204)
    r["HX-Refresh"] = "true"
    return r


def _err(msg):
    return HttpResponse(f'<div class="field-err" role="alert">{msg}</div>', status=422)


def _get(request, pk) -> Invoice:
    return get_object_or_404(services.scoped_invoices(request).select_related("company", "contract", "owner"), pk=pk)


def _owners(request):
    scope = request.membership.scope_for("invoices.create") or request.membership.scope_for("invoices.edit") or "own"
    qs = Membership.objects.filter(tenant=request.tenant, is_active=True).select_related("user").order_by("user__name")
    if scope == "team":
        qs = qs.filter(user_id__in=request.membership.team_user_ids())
    elif scope == "own":
        qs = qs.filter(user=request.user)
    return [m.user for m in qs]


# ---------- list / ageing ----------

@login_required
@require_perm("invoices.view")
def index(request):
    today = timezone.localdate()
    status = request.GET.get("status", "open")
    q = (request.GET.get("q") or "").strip()
    owner = request.GET.get("owner", "")
    base = services.scoped_invoices(request)
    qs = base
    if status == "open":
        qs = qs.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PART])
    elif status == "overdue":
        qs = qs.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PART], due_date__lt=today)
    elif status != "all":
        qs = qs.filter(status=status)
    if owner.isdigit():
        qs = qs.filter(owner_id=int(owner))
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(bill_to_name__icontains=q) | Q(company__name__icontains=q) | Q(bill_to_gstin__icontains=q))
    page = Paginator(qs.order_by("-issue_date", "-id"), 25).get_page(request.GET.get("page"))
    counts = {"open": base.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PART]).count(), "overdue": base.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PART], due_date__lt=today).count(),
              "draft": base.filter(status="draft").count(), "paid": base.filter(status="paid").count(), "cancelled": base.filter(status="cancelled").count(), "all": base.count()}
    summary = services.outstanding_summary(base, today)
    fy_start = today.replace(month=4, day=1) if today.month >= 4 else today.replace(year=today.year - 1, month=4, day=1)
    billed_fy = base.filter(issue_date__gte=fy_start).exclude(status__in=["draft", "cancelled"]).aggregate(t=Sum("total_paise"))["t"] or 0
    cfg = InvoiceSettings.for_tenant(request.tenant)
    return render(request, "invoices/index.html", {"page": page, "status": status, "q": q, "owner": owner, "owners": _owners(request), "counts": counts, "summary": summary, "billed_fy": billed_fy, "today": today,
                                                   "can_create": has_perm(request, "invoices.create"), "cfg_ok": cfg.is_complete, "can_settings": has_perm(request, "settings.manage")})


@login_required
@require_perm("invoices.view")
def ageing(request):
    today = timezone.localdate()
    base = services.scoped_invoices(request)
    data = services.ageing(base, today)
    payments = list(Payment.objects.filter(invoice__in=base).select_related("invoice", "invoice__company", "created_by").order_by("-paid_on", "-id")[:15]) if has_perm(request, "payments.view") or has_perm(request, "invoices.view") else []
    month_start = today.replace(day=1)
    received_month = Payment.objects.filter(invoice__in=base, paid_on__gte=month_start).aggregate(t=Sum("amount_paise"))["t"] or 0
    return render(request, "invoices/ageing.html", {"data": data, "buckets": services.BUCKETS, "payments": payments, "received_month": received_month, "today": today, "can_export": has_perm(request, "data.export")})


# ---------- create / edit ----------

@login_required
@require_perm("invoices.create")
def new(request):
    cfg = InvoiceSettings.for_tenant(request.tenant)
    contract = None
    company = None
    if request.GET.get("contract", "").isdigit():
        contract = Contract.objects.filter(pk=int(request.GET["contract"])).select_related("company", "product", "owner").first()
        company = contract.company if contract else None
    elif request.GET.get("company", "").isdigit():
        company = Company.objects.filter(pk=int(request.GET["company"])).first()
    if company is None:
        return render(request, "invoices/pick_company.html", {"companies": Company.objects.order_by("name")[:500], "cfg_ok": cfg.is_complete})
    inv = services.new_draft(request, company, contract, cfg)
    audit(request, "invoice.draft", inv, after={"company": company.id, "contract": contract.id if contract else None})
    return redirect("invoices:edit", pk=inv.pk)


@login_required
def edit(request, pk):
    inv = _get(request, pk)
    if inv.status != Invoice.Status.DRAFT:
        messages.info(request, "Issued invoices cannot be edited. Cancel it and raise a new one if something is wrong.")
        return redirect("invoices:detail", pk=inv.pk)
    if not services.can_edit(request, inv):
        raise PermissionDenied("You cannot edit this invoice.")
    cfg = InvoiceSettings.for_tenant(request.tenant)
    initial = {f: getattr(inv, f) for f in InvoiceHeadForm.base_fields}
    form = InvoiceHeadForm(request.POST or None, initial=initial)
    line_err = None
    if request.method == "POST":
        lines, line_err = parse_lines(request.POST)
        if form.is_valid() and not line_err:
            for f, v in form.cleaned_data.items():
                setattr(inv, f, v)
            inv.supplier_state = cfg.state_code
            inv.save()
            inv.lines.all().delete()
            for l in lines:
                InvoiceLine(invoice=inv, **l).save()
            inv.recompute()
            audit(request, "invoice.edit", inv, after={"total": inv.total_paise, "lines": len(lines)})
            if request.POST.get("then") == "issue":
                return redirect("invoices:issue", pk=inv.pk)
            messages.success(request, "Draft saved.")
            return redirect("invoices:detail", pk=inv.pk)
    products = list(Product.objects.filter(is_active=True))
    lines_json = json.dumps([{"product": l.product_id or "", "desc": l.description, "hsn": l.hsn_sac, "qty": float(l.quantity), "rate": l.rate_paise / 100, "gst": float(l.gst_rate)} for l in inv.lines.all()] or [{"product": "", "desc": "", "hsn": "", "qty": 1, "rate": "", "gst": 18}])
    products_json = json.dumps({str(p.pk): {"desc": p.name, "hsn": p.hsn_sac, "rate": p.default_price_paise / 100, "gst": float(p.gst_rate)} for p in products})
    return render(request, "invoices/form.html", {"inv": inv, "form": form, "line_err": line_err, "products": products, "lines_json": lines_json, "products_json": products_json, "states": STATE_CHOICES, "cfg": cfg, "supplier_state_name": STATE_CODES.get(cfg.state_code, "not set")})


@login_required
@require_perm("invoices.view")
def detail(request, pk):
    inv = _get(request, pk)
    cfg = InvoiceSettings.for_tenant(request.tenant)
    tax_rows = []
    for rate, taxable, tax in inv.tax_summary():
        if inv.tax_mode == "inter":
            tax_rows.append((f"IGST @{float(rate):g}%", tax))
        else:
            h = tax // 2
            tax_rows += [(f"CGST @{float(rate) / 2:g}%", h), (f"SGST @{float(rate) / 2:g}%", tax - h)]
    ctx = {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.select_related("created_by"), "tax_rows": tax_rows, "cfg": cfg, "today": timezone.localdate(),
           "can_edit": services.can_edit(request, inv), "can_pay": has_perm(request, "payments.create"), "state_name": STATE_CODES.get(inv.place_of_supply, ""), "supplier_state_name": STATE_CODES.get(inv.supplier_state, ""),
           "pay_form": PaymentForm(invoice=inv) if inv.is_open else None}
    return render(request, "invoices/detail.html", ctx)


@login_required
def issue(request, pk):
    inv = _get(request, pk)
    if not services.can_edit(request, inv) or inv.status != Invoice.Status.DRAFT:
        raise PermissionDenied("Only a draft you can edit may be issued.")
    cfg = InvoiceSettings.for_tenant(request.tenant)
    problems = []
    if not cfg.is_complete:
        problems.append("Supplier address and state are missing on the Workspace page.")
    if not inv.lines.exists():
        problems.append("The invoice has no lines.")
    if not inv.place_of_supply:
        problems.append("Pick the place of supply.")
    if request.method == "POST" and not problems:
        services.issue(inv, request.user, cfg)
        audit(request, "invoice.issue", inv, after={"number": inv.number, "total": inv.total_paise, "tax_mode": inv.tax_mode})
        messages.success(request, f"Invoice {inv.number} issued.")
        return redirect("invoices:detail", pk=inv.pk)
    return render(request, "invoices/_issue_drawer.html", {"inv": inv, "problems": problems, "cfg": cfg, "next_number": f"{cfg.series_prefix}/{inv.fy_label(inv.issue_date)}/…"})


@login_required
@require_POST
def cancel(request, pk):
    inv = _get(request, pk)
    if not services.can_edit(request, inv) or inv.status == Invoice.Status.CANCELLED:
        raise PermissionDenied("You cannot cancel this invoice.")
    if inv.received_paise:
        messages.error(request, "This invoice has payments against it. Remove them first or issue a credit instead.")
        return redirect("invoices:detail", pk=inv.pk)
    services.cancel(inv, request.user, request.POST.get("reason", ""))
    audit(request, "invoice.cancel", inv, after={"reason": inv.cancel_reason})
    messages.success(request, f"{inv} cancelled. The number stays in the series.")
    return redirect("invoices:detail", pk=inv.pk)


@login_required
@require_POST
def delete_draft(request, pk):
    inv = _get(request, pk)
    if inv.status != Invoice.Status.DRAFT or not services.can_edit(request, inv):
        raise PermissionDenied("Only drafts can be deleted.")
    audit(request, "invoice.delete_draft", inv, before={"company": inv.company_id, "total": inv.total_paise})
    inv.lines.all().delete()
    inv.delete()
    messages.success(request, "Draft deleted.")
    return redirect("invoices:index")


# ---------- pdf / email ----------

@login_required
@require_perm("invoices.view")
def pdf(request, pk):
    inv = _get(request, pk)
    cfg = InvoiceSettings.for_tenant(request.tenant)
    buf = build_invoice_pdf(inv, cfg, request.tenant)
    name = (inv.number or f"draft-{inv.pk}").replace("/", "-") + ".pdf"
    resp = FileResponse(buf, content_type="application/pdf", as_attachment=request.GET.get("dl") == "1", filename=name)
    return resp


@login_required
def send(request, pk):
    inv = _get(request, pk)
    if inv.status == Invoice.Status.DRAFT or inv.status == Invoice.Status.CANCELLED:
        raise PermissionDenied("Issue the invoice before sending it.")
    if not (services.can_edit(request, inv) or has_perm(request, "invoices.create")):
        raise PermissionDenied("You cannot send this invoice.")
    cfg = InvoiceSettings.for_tenant(request.tenant)
    if request.method == "POST":
        to = (request.POST.get("to") or "").strip()
        if "@" not in to:
            return _err("Enter the client's email address.")
        ok = services.email_invoice(inv, cfg, request.tenant, to, request.user, request.POST.get("message", ""))
        if not ok:
            return _err("The email could not be sent. Check the mail settings and try again.")
        if to != inv.bill_to_email:
            inv.bill_to_email = to
            inv.save(update_fields=["bill_to_email", "updated_at"])
        audit(request, "invoice.send", inv, after={"to": to})
        messages.success(request, f"Invoice {inv.number} emailed to {to}.")
        return _refresh()
    return render(request, "invoices/_send_drawer.html", {"inv": inv, "cfg": cfg, "to": inv.bill_to_email or inv.company.email or (inv.contract.contact_email if inv.contract_id else "")})


# ---------- payments ----------

@login_required
@require_perm("payments.create")
def payment_add(request, pk):
    inv = _get(request, pk)
    if not inv.is_open:
        raise PermissionDenied("Payments go against issued invoices with a balance.")
    form = PaymentForm(request.POST or None, invoice=inv)
    if request.method == "POST":
        if not form.is_valid():
            return _err("; ".join(e[0] for e in form.errors.values()))
        d = form.cleaned_data
        p = services.record_payment(inv, request.user, form.fields["amount"].to_paise(d["amount"]), d["mode"], d.get("reference", ""), d["paid_on"], d.get("notes", ""))
        audit(request, "payment.record", p, after={"invoice": inv.number, "amount": p.amount_paise, "mode": p.mode, "ref": p.reference})
        messages.success(request, f"{p.get_mode_display()} payment of ₹{p.amount_paise / 100:,.2f} recorded." + (" Invoice fully paid." if inv.status == Invoice.Status.PAID else ""))
        return _refresh()
    return render(request, "invoices/_payment_drawer.html", {"inv": inv, "form": form})


@login_required
@require_perm("payments.create")
@require_POST
def payment_delete(request, pk, payment_id):
    inv = _get(request, pk)
    p = get_object_or_404(Payment, pk=payment_id, invoice=inv)
    if not services.can_edit(request, inv):
        raise PermissionDenied("You cannot remove payments on this invoice.")
    audit(request, "payment.delete", p, before={"invoice": inv.number, "amount": p.amount_paise, "mode": p.mode, "ref": p.reference})
    services.delete_payment(p)
    messages.success(request, "Payment removed.")
    return redirect("invoices:detail", pk=inv.pk)


# ---------- accounting exports ----------

@login_required
@require_perm("data.export")
def export(request, fmt):
    base = services.scoped_invoices(request)
    frm, to = request.GET.get("from"), request.GET.get("to")
    if frm:
        base = base.filter(issue_date__gte=frm)
    if to:
        base = base.filter(issue_date__lte=to)
    label = f"{frm or 'start'}_{to or 'today'}"
    if fmt == "zoho":
        body = services.zoho_books_csv(base)
        resp = HttpResponse("﻿" + body, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="zoho-books-invoices-{label}.csv"'
    elif fmt == "tally":
        resp = HttpResponse(services.tally_xml(base, request.tenant.name), content_type="application/xml")
        resp["Content-Disposition"] = f'attachment; filename="tally-sales-vouchers-{label}.xml"'
    else:
        return HttpResponse(status=404)
    audit(request, f"export.invoices_{fmt}", note=label)
    return resp


# ---------- settings (on the Workspace page) ----------

@login_required
@require_perm("settings.manage")
@require_POST
def settings_save(request):
    cfg = InvoiceSettings.for_tenant(request.tenant)
    form = InvoiceSettingsForm(request.POST, request.FILES, instance=cfg)
    if not form.is_valid():
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("tenants:workspace")
    before = {"series": cfg.series_prefix, "state": cfg.state_code}
    obj = form.save()
    audit(request, "workspace.invoice_settings", request.tenant, before=before, after={"series": obj.series_prefix, "state": obj.state_code, "upi": obj.upi_id})
    messages.success(request, "Invoice settings saved.")
    return redirect(f"{request.META.get('HTTP_REFERER', '/')}#invoicing" if "workspace" in request.META.get("HTTP_REFERER", "") else "tenants:workspace")
