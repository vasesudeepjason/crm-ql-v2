import csv
import io

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.approvals.models import Approval
from apps.core.audit import audit
from apps.core.permissions import require_perm
from apps.crm.models import Activity, Company, Lead
from apps.field.models import Visit

from . import services

TABS = [("pipeline", "Pipeline"), ("conversion", "Conversion"), ("team", "Team"), ("activity", "Activity")]


@login_required
@require_perm("reports.view")
def index(request):
    tab = request.GET.get("tab", "pipeline")
    if tab not in dict(TABS):
        tab = "pipeline"
    start, end, code, label, s_date, e_date = services.period(request)
    owner = request.GET.get("owner", "")
    owner_id = int(owner) if owner.isdigit() else None
    ctx = {"tab": tab, "tabs": TABS, "period": code, "period_label": label, "from": s_date, "to": e_date, "owner": owner, "owners": services.team_users(request),
           "qs": f"period={code}&owner={owner}" + (f"&from={s_date}&to={e_date}" if code == "custom" else "")}
    if tab == "pipeline":
        ctx["p"] = services.pipeline(request, owner_id)
    elif tab == "conversion":
        ctx["c"] = services.conversion(request, start, end, owner_id)
        ctx["max_step"] = max([st["n"] for st in ctx["c"]["steps"]] or [1]) or 1
    elif tab == "team":
        ctx["rows"] = services.team(request, start, end)
    else:
        ctx["trend"] = services.activity_trend(request, start, end, owner_id)
        ctx["trend_max"] = max([max(d["leads"], d["calls"], d["visits"]) for d in ctx["trend"]] or [1]) or 1
    return render(request, "reports/index.html", ctx)


# ---------- exports (section 3: every export is written to the audit log) ----------

def _csv_response(name, header, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    resp = HttpResponse("﻿" + out.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{name}-{timezone.localdate():%Y%m%d}.csv"'
    return resp


@login_required
@require_perm("data.export")
def export(request, what):
    start, end, code, label, *_ = services.period(request)
    owner = request.GET.get("owner", "")
    owner_id = int(owner) if owner.isdigit() else None
    users = [u.id for u in services.team_users(request)]
    if what == "leads":
        qs = services.lead_scope(request, owner_id).select_related("company", "owner").order_by("-created_at")
        if request.GET.get("stage"):
            qs = qs.filter(stage=request.GET["stage"])
        if request.GET.get("period"):
            qs = qs.filter(created_at__gte=start, created_at__lt=end)
        rows = ((l.id, l.name, l.company.name if l.company else "", l.phone, l.email, l.get_stage_display(), l.owner.name if l.owner else "", l.source, (l.est_value_paise or 0) / 100, l.next_follow_up or "", timezone.localtime(l.created_at).strftime("%Y-%m-%d %H:%M"), l.won_at and timezone.localtime(l.won_at).strftime("%Y-%m-%d") or "", l.lost_reason) for l in qs)
        resp = _csv_response("leads", ["ID", "Lead", "Company", "Phone", "Email", "Stage", "Owner", "Source", "Value (₹)", "Next follow-up", "Created", "Won on", "Lost reason"], rows)
    elif what == "companies":
        qs = Company.objects.select_related("owner").order_by("name")
        rows = ((c.id, c.name, c.gstin, c.phone, c.email, c.city, c.state, c.owner.name if c.owner else "", c.source, ", ".join(c.tags or []), timezone.localtime(c.created_at).strftime("%Y-%m-%d")) for c in qs)
        resp = _csv_response("companies", ["ID", "Company", "GSTIN", "Phone", "Email", "City", "State", "Owner", "Source", "Tags", "Created"], rows)
    elif what == "calls":
        qs = Activity.objects.filter(type="call", created_by_id__in=users, created_at__gte=start, created_at__lt=end).select_related("lead", "created_by").order_by("-created_at")
        rows = ((timezone.localtime(a.created_at).strftime("%Y-%m-%d %H:%M"), a.created_by.name if a.created_by else "", a.lead.name if a.lead else "", a.lead.phone if a.lead else "", a.get_outcome_display(), round((a.duration_seconds or 0) / 60, 1), a.notes) for a in qs)
        resp = _csv_response("calls", ["When", "Caller", "Lead", "Phone", "Outcome", "Minutes", "Notes"], rows)
    elif what == "visits":
        qs = Visit.objects.filter(executive_id__in=users, captured_at__gte=start, captured_at__lt=end).select_related("executive", "company", "lead").order_by("-captured_at")
        rows = ((timezone.localtime(v.captured_at).strftime("%Y-%m-%d %H:%M"), v.executive.name, v.title, v.contact_name, v.get_outcome_display(), v.get_status_display(), v.lat or "", v.lng or "", v.accuracy or "", v.duration_minutes or "", v.next_follow_up or "", v.notes) for v in qs)
        resp = _csv_response("visits", ["When", "Executive", "Visited", "Met", "Outcome", "Status", "Lat", "Lng", "Accuracy (m)", "Minutes", "Next follow-up", "Notes"], rows)
    elif what == "contracts":
        from apps.contracts.services import scoped_contracts

        qs = scoped_contracts(request).select_related("contact").order_by("end_date")
        if request.GET.get("status") and request.GET["status"] != "all":
            qs = qs.filter(status=request.GET["status"])
        rows = ((c.number, c.company.name, c.company.gstin, c.product.name, c.product.hsn_sac, str(c.product.gst_rate), c.title, c.start_date, c.end_date, c.get_billing_cycle_display(), c.amount_paise / 100, c.annual_value_paise / 100, "yes" if c.auto_renew else "no", c.owner.name if c.owner else "", c.contact_email, c.get_status_display(), c.lapsed_reason) for c in qs)
        resp = _csv_response("contracts", ["Number", "Company", "GSTIN", "Product", "HSN/SAC", "GST %", "Title", "Start", "End", "Cycle", "Amount (₹)", "Per year (₹)", "Auto-renew", "Owner", "Client email", "Status", "Lapsed reason"], rows)
    elif what in ("invoices", "payments", "ageing"):
        from apps.invoices import services as inv_services
        from apps.invoices.gst import STATE_CODES
        from apps.invoices.models import Payment

        base = inv_services.scoped_invoices(request)
        if what == "invoices":
            qs = base.order_by("-issue_date")
            rows = ((i.number or "DRAFT", i.issue_date, i.due_date, i.get_status_display(), i.bill_to_name, i.bill_to_gstin, STATE_CODES.get(i.place_of_supply, ""), i.get_tax_mode_display(), i.subtotal_paise / 100, i.cgst_paise / 100, i.sgst_paise / 100, i.igst_paise / 100, i.round_off_paise / 100, i.total_paise / 100, i.received_paise / 100, i.balance_paise / 100, i.owner.name if i.owner else "", i.contract.number if i.contract_id else "") for i in qs)
            resp = _csv_response("invoices", ["Number", "Date", "Due", "Status", "Customer", "GSTIN", "Place of supply", "Tax", "Taxable (₹)", "CGST (₹)", "SGST (₹)", "IGST (₹)", "Round off (₹)", "Total (₹)", "Received (₹)", "Balance (₹)", "Owner", "Contract"], rows)
        elif what == "payments":
            qs = Payment.objects.filter(invoice__in=base).select_related("invoice", "created_by").order_by("-paid_on")
            rows = ((p.paid_on, p.invoice.number, p.invoice.bill_to_name, p.get_mode_display(), p.reference, p.amount_paise / 100, p.created_by.name if p.created_by else "", p.notes) for p in qs)
            resp = _csv_response("payments", ["Date", "Invoice", "Customer", "Mode", "Reference", "Amount (₹)", "Recorded by", "Notes"], rows)
        else:
            data = inv_services.ageing(base)
            rows = ((r["company"].name, r["n"], r["current"] / 100, r["1-30"] / 100, r["31-60"] / 100, r["61-90"] / 100, r["90+"] / 100, r["total"] / 100, r["oldest"]) for r in data["rows"])
            resp = _csv_response("ageing", ["Customer", "Open invoices", "Not due (₹)", "1-30 (₹)", "31-60 (₹)", "61-90 (₹)", "90+ (₹)", "Total due (₹)", "Oldest days late"], rows)
    elif what == "approvals":
        qs = Approval.objects.filter(submitter_id__in=users, submitted_at__gte=start, submitted_at__lt=end).select_related("submitter", "reviewer").order_by("-submitted_at")
        rows = ((a.id, a.get_kind_display(), a.title, a.company_name, a.submitter.name, a.get_status_display(), a.reviewer.name if a.reviewer else "", timezone.localtime(a.submitted_at).strftime("%Y-%m-%d %H:%M"), a.decided_at and timezone.localtime(a.decided_at).strftime("%Y-%m-%d %H:%M") or "", a.reason) for a in qs)
        resp = _csv_response("approvals", ["ID", "Kind", "Title", "Company", "Submitted by", "Status", "Decided by", "Submitted", "Decided", "Reason"], rows)
    else:
        return HttpResponse(status=404)
    audit(request, f"export.{what}", note=f"{label}" + (f" · owner {owner_id}" if owner_id else ""))
    return resp
