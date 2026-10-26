"""Scope, drafting from a contract, issue / cancel, payments, email, ageing and accounting exports."""
from __future__ import annotations

import csv
import io
import logging
from datetime import timedelta
from decimal import Decimal
from xml.sax.saxutils import escape as xesc

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Sum
from django.utils import timezone

from apps.core.money import rupees
from apps.core.permissions import scope_for

from .gst import STATE_CODES, state_code_from_gstin, state_code_from_name
from .models import Invoice, InvoiceLine, InvoiceSettings, Payment
from .pdf import build_invoice_pdf

log = logging.getLogger(__name__)


def scoped_invoices(request, qs=None):
    qs = Invoice.objects.select_related("company", "owner", "contract") if qs is None else qs
    scope = scope_for(request, "invoices.view")
    if scope == "all":
        return qs
    if scope == "team":
        return qs.filter(owner_id__in=request.membership.team_user_ids())
    if scope == "own":
        return qs.filter(owner_id=request.user.id)
    return qs.none()


def can_edit(request, inv: Invoice) -> bool:
    scope = scope_for(request, "invoices.edit")
    if scope == "all":
        return True
    if scope == "team":
        return inv.owner_id in request.membership.team_user_ids()
    if scope == "own":
        return inv.owner_id == request.user.id
    # a Sales Executive may edit their own drafts (invoices.create own)
    return scope_for(request, "invoices.create") == "own" and inv.owner_id == request.user.id and inv.status == Invoice.Status.DRAFT


def buyer_snapshot(company) -> dict:
    code = state_code_from_gstin(company.gstin) or state_code_from_name(company.state)
    addr = ", ".join(x for x in [company.address.strip() if company.address else "", company.city, company.state] if x)
    return {"bill_to_name": company.name, "bill_to_address": addr, "bill_to_gstin": company.gstin or "", "bill_to_state": code, "bill_to_email": company.email or "", "place_of_supply": code}


def new_draft(request, company, contract=None, cfg: InvoiceSettings | None = None) -> Invoice:
    cfg = cfg or InvoiceSettings.for_tenant(request.tenant)
    today = timezone.localdate()
    inv = Invoice(company=company, contract=contract, owner=(contract.owner if contract and contract.owner_id else request.user), issue_date=today, due_date=today + timedelta(days=cfg.due_days),
                  supplier_state=cfg.state_code, notes=cfg.default_notes, terms=cfg.default_terms, **buyer_snapshot(company))
    if contract:
        inv.period_start, inv.period_end = contract.start_date, contract.end_date
        if contract.contact_email:
            inv.bill_to_email = contract.contact_email
    inv.created_by = request.user
    inv.save()
    if contract:
        InvoiceLine(invoice=inv, product=contract.product, description=contract.display_title + (f" · {contract.start_date:%d %b %Y} to {contract.end_date:%d %b %Y}" if contract.billing_cycle in ("yearly", "one_time", "half_yearly", "quarterly") else ""),
                    hsn_sac=contract.product.hsn_sac, quantity=Decimal("1"), rate_paise=contract.amount_paise, gst_rate=contract.product.gst_rate, position=0).save()
    inv.recompute()
    return inv


def issue(inv: Invoice, by, cfg: InvoiceSettings):
    inv.assign_number(cfg.series_prefix or "INV")
    inv.status = Invoice.Status.ISSUED
    inv.issued_at = timezone.now()
    inv.save(update_fields=["number", "status", "issued_at", "updated_at"])
    inv.recompute()


def cancel(inv: Invoice, by, reason: str):
    inv.status = Invoice.Status.CANCELLED
    inv.cancel_reason = reason.strip()[:255]
    inv.save(update_fields=["status", "cancel_reason", "updated_at"])


def record_payment(inv: Invoice, by, amount_paise: int, mode: str, reference: str, paid_on, notes: str = "") -> Payment:
    p = Payment(invoice=inv, amount_paise=amount_paise, mode=mode, reference=reference.strip(), paid_on=paid_on, notes=notes.strip())
    p.created_by = by
    p.save()
    inv.recompute()
    return p


def delete_payment(p: Payment):
    inv = p.invoice
    p.delete()
    inv.recompute()


def email_invoice(inv: Invoice, cfg: InvoiceSettings, tenant, to: str, by, message: str = "") -> bool:
    pdf = build_invoice_pdf(inv, cfg, tenant)
    supplier = cfg.legal_name or tenant.name
    body = (message.strip() + "\n\n" if message.strip() else "") + (
        f"Dear {inv.bill_to_name},\n\nPlease find attached invoice {inv.number} dated {inv.issue_date:%d %b %Y} for {rupees(inv.total_paise)}, due on {inv.due_date:%d %b %Y}."
        + (f"\n\nPay by UPI to {cfg.upi_id} or by bank transfer (details on the invoice)." if cfg.upi_id or cfg.bank_account_no else "") + f"\n\nRegards,\n{by.name if by else supplier}\n{supplier}")
    msg = EmailMessage(f"Invoice {inv.number} from {supplier} — {rupees(inv.total_paise)}", body, settings.DEFAULT_FROM_EMAIL, [to], reply_to=[cfg.email] if cfg.email else None)
    msg.attach(f"{inv.number.replace('/', '-')}.pdf", pdf.getvalue(), "application/pdf")
    try:
        msg.send()
    except Exception:
        log.exception("invoice email failed")
        return False
    inv.sent_at = timezone.now()
    inv.save(update_fields=["sent_at", "updated_at"])
    return True


# ---------- ageing ----------

BUCKETS = [("current", "Not yet due"), ("1-30", "1–30 days"), ("31-60", "31–60 days"), ("61-90", "61–90 days"), ("90+", "Over 90 days")]


def ageing(qs, today=None) -> dict:
    """Outstanding balance per company per bucket, plus totals. Only issued / partly paid invoices."""
    today = today or timezone.localdate()
    rows: dict = {}
    totals = {k: 0 for k, _ in BUCKETS}
    for inv in qs.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PART]).select_related("company"):
        b = inv.age_bucket
        r = rows.setdefault(inv.company_id, {"company": inv.company, "n": 0, "total": 0, "oldest": 0, **{k: 0 for k, _ in BUCKETS}})
        r[b] += inv.balance_paise
        r["total"] += inv.balance_paise
        r["n"] += 1
        r["oldest"] = max(r["oldest"], inv.days_overdue)
        totals[b] += inv.balance_paise
    out = sorted(rows.values(), key=lambda r: (-r["oldest"], -r["total"]))
    return {"rows": out, "totals": totals, "grand": sum(totals.values()), "overdue": sum(v for k, v in totals.items() if k != "current")}


def outstanding_summary(qs, today=None) -> dict:
    today = today or timezone.localdate()
    open_q = qs.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PART])
    agg = open_q.aggregate(t=Sum("total_paise"), r=Sum("received_paise"))
    overdue_q = open_q.filter(due_date__lt=today)
    oagg = overdue_q.aggregate(t=Sum("total_paise"), r=Sum("received_paise"))
    return {"outstanding": (agg["t"] or 0) - (agg["r"] or 0), "open_n": open_q.count(), "overdue": (oagg["t"] or 0) - (oagg["r"] or 0), "overdue_n": overdue_q.count()}


# ---------- accounting exports ----------

def zoho_books_csv(qs) -> str:
    """One row per line, in the column names Zoho Books' invoice import expects."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Invoice Number", "Invoice Date", "Due Date", "Invoice Status", "Customer Name", "GST Treatment", "GST Identification Number (GSTIN)", "Place of Supply", "Item Name", "HSN/SAC", "Quantity", "Item Price", "Item Tax %", "Item Tax Amount", "Item Total", "Notes", "Terms & Conditions"])
    for inv in qs.exclude(status=Invoice.Status.DRAFT).prefetch_related("lines"):
        for l in inv.lines.all():
            w.writerow([inv.number, inv.issue_date.isoformat(), inv.due_date.isoformat(), {"paid": "Paid", "cancelled": "Void"}.get(inv.status, "Sent"), inv.bill_to_name, "business_gst" if inv.bill_to_gstin else "consumer", inv.bill_to_gstin, STATE_CODES.get(inv.place_of_supply, ""),
                        l.description, l.hsn_sac, f"{l.quantity:g}", f"{l.rate_paise / 100:.2f}", f"{l.gst_rate:g}", f"{l.tax_paise / 100:.2f}", f"{(l.amount_paise + l.tax_paise) / 100:.2f}", inv.notes, inv.terms.replace("\n", " | ")])
    return buf.getvalue()


def tally_xml(qs, company_name: str) -> str:
    """Sales vouchers in Tally's XML import envelope (Import Data → Vouchers). Ledgers must exist in Tally."""
    vouchers = []
    for inv in qs.exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.CANCELLED]).prefetch_related("lines"):
        d = inv.issue_date.strftime("%Y%m%d")
        total = f"{inv.total_paise / 100:.2f}"
        ledgers = [f"<LEDGERENTRIES.LIST><LEDGERNAME>{xesc(inv.bill_to_name)}</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{total}</AMOUNT></LEDGERENTRIES.LIST>"]
        for rate, taxable, tax in inv.tax_summary():
            ledgers.append(f"<LEDGERENTRIES.LIST><LEDGERNAME>Sales @{float(rate):g}%</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{taxable / 100:.2f}</AMOUNT></LEDGERENTRIES.LIST>")
            if inv.tax_mode == "inter":
                ledgers.append(f"<LEDGERENTRIES.LIST><LEDGERNAME>Output IGST @{float(rate):g}%</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{tax / 100:.2f}</AMOUNT></LEDGERENTRIES.LIST>")
            else:
                h = tax // 2
                ledgers.append(f"<LEDGERENTRIES.LIST><LEDGERNAME>Output CGST @{float(rate) / 2:g}%</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{h / 100:.2f}</AMOUNT></LEDGERENTRIES.LIST>")
                ledgers.append(f"<LEDGERENTRIES.LIST><LEDGERNAME>Output SGST @{float(rate) / 2:g}%</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{(tax - h) / 100:.2f}</AMOUNT></LEDGERENTRIES.LIST>")
        if inv.round_off_paise:
            ledgers.append(f"<LEDGERENTRIES.LIST><LEDGERNAME>Round Off</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{inv.round_off_paise / 100:.2f}</AMOUNT></LEDGERENTRIES.LIST>")
        vouchers.append(f"<TALLYMESSAGE xmlns:UDF=\"TallyUDF\"><VOUCHER VCHTYPE=\"Sales\" ACTION=\"Create\"><DATE>{d}</DATE><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>{xesc(inv.number)}</VOUCHERNUMBER><PARTYLEDGERNAME>{xesc(inv.bill_to_name)}</PARTYLEDGERNAME><PARTYGSTIN>{xesc(inv.bill_to_gstin)}</PARTYGSTIN><PLACEOFSUPPLY>{xesc(STATE_CODES.get(inv.place_of_supply, ''))}</PLACEOFSUPPLY><NARRATION>{xesc(inv.notes)}</NARRATION>{''.join(ledgers)}</VOUCHER></TALLYMESSAGE>")
    return ('<?xml version="1.0" encoding="UTF-8"?><ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>'
            + xesc(company_name) + "</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA>" + "".join(vouchers) + "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")
