from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.contracts.models import Contract, Product
from apps.core.tenancy import rls_bypass, tenant_context
from apps.invoices.gst import amount_to_words, tax_mode
from apps.invoices.models import Invoice, InvoiceLine, InvoiceSettings, Payment
from apps.invoices.services import ageing, tally_xml, zoho_books_csv


@pytest.fixture
def setup(two_tenants):
    """Supplier in Telangana (36); customer A in Telangana, with one contract; tenant B has its own invoice."""
    a, owner = two_tenants["a"], two_tenants["owner_a"]
    with rls_bypass():
        a.gstin = "36AABCS1234A1Z5"
        a.save(update_fields=["gstin"])
    InvoiceSettings.objects.create(tenant=a, legal_name="A Co Pvt Ltd", address="Hyderabad", state_code="36", series_prefix="QL", due_days=15, upi_id="aco@upi", bank_account_no="123", bank_ifsc="HDFC0001234")
    co = two_tenants["lead_a"].company
    today = timezone.localdate()
    with tenant_context(a.id):
        co.gstin, co.state, co.email = "36AABCS1234A1Z5", "Telangana", "accounts@acustomer.in"
        co.save()
        p = Product(name="AMC Gold", hsn_sac="998713", gst_rate=Decimal("18"), default_price_paise=48000_00)
        p.created_by = owner
        p.save()
        c = Contract(company=co, product=p, start_date=today - timedelta(days=10), end_date=today + timedelta(days=354), amount_paise=48000_00, owner=owner, contact_email="priya@acustomer.in")
        c.created_by = owner
        c.save()
    with tenant_context(two_tenants["b"].id):
        ib = Invoice(company=two_tenants["lead_b"].company, owner=two_tenants["owner_b"], bill_to_name="B Customer", due_date=today, status="issued", number="X/26-27/0001", total_paise=100)
        ib.created_by = two_tenants["owner_b"]
        ib.save()
    return dict(co=co, p=p, c=c, today=today, ib=ib)


def test_gst_helpers():
    assert tax_mode("36", "36") == "intra" and tax_mode("36", "27") == "inter" and tax_mode("", "27") == "intra"
    assert amount_to_words(56640_00) == "Fifty Six Thousand Six Hundred Forty Rupees Only"
    assert amount_to_words(1234567_50) == "Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven Rupees and Fifty Paise Only"
    assert amount_to_words(0) == "Zero Rupees Only"


def test_draft_from_contract_issue_number_and_split(two_tenants, setup, login, client):
    client = login(two_tenants["owner_a"])
    r = client.get(reverse("invoices:new"), {"contract": setup["c"].id})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        inv = Invoice.objects.get(contract=setup["c"])
        assert inv.status == "draft" and inv.number == "" and inv.bill_to_gstin == "36AABCS1234A1Z5" and inv.place_of_supply == "36" and inv.bill_to_email == "priya@acustomer.in"
        assert inv.subtotal_paise == 48000_00 and inv.cgst_paise == 4320_00 and inv.sgst_paise == 4320_00 and inv.igst_paise == 0 and inv.total_paise == 56640_00
        assert inv.due_date == inv.issue_date + timedelta(days=15) and inv.period_end == setup["c"].end_date
    # edit: add a second line at 28%, change place of supply to Maharashtra → IGST, per-rate summary
    r = client.post(reverse("invoices:edit", args=[inv.id]), {"issue_date": setup["today"], "due_date": setup["today"] + timedelta(days=30), "bill_to_name": "A Customer", "bill_to_address": "Mumbai", "bill_to_gstin": "", "bill_to_state": "27", "bill_to_email": "x@y.in", "place_of_supply": "27", "terms": "Pay in 30 days",
                                                                 "line_product": [str(setup["p"].id), ""], "line_desc": ["AMC Gold", "Spare kit"], "line_hsn": ["998713", "8479"], "line_qty": ["1", "2"], "line_rate": ["48000", "850.50"], "line_gst": ["18", "28"]})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.tax_mode == "inter" and inv.lines.count() == 2
        assert inv.subtotal_paise == 48000_00 + 1701_00
        assert inv.igst_paise == 8640_00 + 476_28 and inv.cgst_paise == 0
        assert inv.total_paise == 58817_00 and inv.round_off_paise == -28  # 58817.28 rounds to 58817.00
        assert [(float(r), t) for r, _, t in inv.tax_summary()] == [(18.0, 8640_00), (28.0, 476_28)]
    # a validation error re-renders the form (200) with the message
    r = client.post(reverse("invoices:edit", args=[inv.id]), {"issue_date": setup["today"], "due_date": setup["today"] - timedelta(days=1), "bill_to_name": "A", "place_of_supply": "27", "line_desc": ["x"], "line_qty": ["1"], "line_rate": ["1"], "line_gst": ["18"]})
    assert r.status_code == 200 and b"before the invoice date" in r.content
    assert client.post(reverse("invoices:edit", args=[inv.id]), {"issue_date": setup["today"], "due_date": setup["today"], "bill_to_name": "A", "place_of_supply": "27", "line_desc": [""], "line_qty": [""], "line_rate": [""], "line_gst": ["18"]}).status_code == 200
    # issue → numbered in the QL series for this FY, locked
    assert b"Issue" in client.get(reverse("invoices:issue", args=[inv.id])).content
    r = client.post(reverse("invoices:issue", args=[inv.id]))
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.status == "issued" and inv.number == f"QL/{Invoice.fy_label(inv.issue_date)}/0001" and inv.issued_at
    assert client.get(reverse("invoices:edit", args=[inv.id])).status_code == 302  # no editing once issued
    # second invoice gets 0002; tenant B's series is separate
    r = client.get(reverse("invoices:new"), {"company": setup["co"].id})
    with tenant_context(two_tenants["a"].id):
        inv2 = Invoice.objects.filter(status="draft").latest("id")
        InvoiceLine(invoice=inv2, description="Visit", rate_paise=1000_00, gst_rate=18).save()
        inv2.recompute()
    assert client.post(reverse("invoices:issue", args=[inv2.id])).status_code == 302
    with tenant_context(two_tenants["a"].id):
        inv2.refresh_from_db()
        assert inv2.number.endswith("/0002")
    return inv


def test_payments_part_full_and_ageing(two_tenants, setup, login, client):
    client = login(two_tenants["owner_a"])
    client.get(reverse("invoices:new"), {"contract": setup["c"].id})
    with tenant_context(two_tenants["a"].id):
        inv = Invoice.objects.get(contract=setup["c"])
        inv.due_date = setup["today"] - timedelta(days=40)
        inv.save()
    client.post(reverse("invoices:issue", args=[inv.id]))
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.is_overdue and inv.age_bucket == "31-60"
    body = client.get(reverse("invoices:ageing")).content.decode()
    assert "A Customer" in body and "oldest 40 days late" in body
    # part payment
    r = client.post(reverse("invoices:payment_add", args=[inv.id]), {"amount": "20000", "mode": "upi", "paid_on": setup["today"], "reference": "UPI123"})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.status == "part_paid" and inv.received_paise == 20000_00 and inv.balance_paise == 36640_00
    # overpayment refused
    assert client.post(reverse("invoices:payment_add", args=[inv.id]), {"amount": "40000", "mode": "cash", "paid_on": setup["today"]}).status_code == 422
    # rest → paid, no longer in ageing
    assert client.post(reverse("invoices:payment_add", args=[inv.id]), {"amount": "36640", "mode": "bank", "paid_on": setup["today"], "reference": "UTR9"}).status_code == 204
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.status == "paid" and inv.paid_at and inv.balance_paise == 0
        assert ageing(Invoice.objects.all())["grand"] == 0
        p = inv.payments.first()
    # removing a payment reopens it
    assert client.post(reverse("invoices:payment_delete", args=[inv.id, p.id])).status_code == 302
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.status == "part_paid"
    # cancelling with payments is refused; detail shows payments
    r = client.post(reverse("invoices:cancel", args=[inv.id]), {"reason": "oops"}, follow=True)
    assert b"has payments against it" in r.content
    with rls_bypass():
        from apps.core.models import AuditLog

        assert AuditLog.objects.filter(action="payment.record").count() == 2 and AuditLog.objects.filter(action="payment.delete").count() == 1


def test_pdf_email_and_scope(two_tenants, setup, login, client):
    client = login(two_tenants["owner_a"])
    client.get(reverse("invoices:new"), {"contract": setup["c"].id})
    with tenant_context(two_tenants["a"].id):
        inv = Invoice.objects.get(contract=setup["c"])
    assert client.get(reverse("invoices:send", args=[inv.id])).status_code == 403  # draft cannot be emailed
    client.post(reverse("invoices:issue", args=[inv.id]))
    r = client.get(reverse("invoices:pdf", args=[inv.id]))
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    pdf = b"".join(r.streaming_content)
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
    mail.outbox.clear()
    r = client.post(reverse("invoices:send", args=[inv.id]), {"to": "priya@acustomer.in", "message": "As discussed"})
    assert r.status_code == 204 and len(mail.outbox) == 1
    m = mail.outbox[0]
    assert m.to == ["priya@acustomer.in"] and "56,640" in m.subject and m.attachments[0][0].endswith(".pdf") and m.attachments[0][2] == "application/pdf" and "As discussed" in m.body
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.sent_at
    # scope: tenant B cannot see it; manager (team) does not see the owner's invoice; telecaller has no access
    client.logout()
    client = login(two_tenants["owner_b"])
    assert client.get(reverse("invoices:detail", args=[inv.id])).status_code == 404
    assert b"X/26-27/0001" in client.get(reverse("invoices:index"), {"status": "all"}).content
    client.logout()
    client = login(two_tenants["mgr_a"])
    assert b"QL/" not in client.get(reverse("invoices:index"), {"status": "all"}).content
    client.logout()
    client = login(two_tenants["tc_a"])
    assert client.get(reverse("invoices:index")).status_code == 403


def test_settings_and_accounting_exports(two_tenants, setup, login, client):
    client = login(two_tenants["owner_a"])
    r = client.post(reverse("invoices:settings"), {"legal_name": "A Co Pvt Ltd", "address": "Hyderabad", "state_code": "36", "pan": "bad", "series_prefix": "ql/x", "due_days": 30, "default_terms": "T1\nT2"}, follow=True)
    assert b"PAN looks wrong" in r.content
    r = client.post(reverse("invoices:settings"), {"legal_name": "A Co Pvt Ltd", "address": "Hyderabad", "state_code": "36", "pan": "AABCS1234A", "series_prefix": "ql/x", "due_days": 30, "default_terms": "T1\nT2", "bank_ifsc": "HDFC0001234"}, follow=True)
    assert b"Invoice settings saved" in r.content
    cfg = InvoiceSettings.objects.get(tenant=two_tenants["a"])
    assert cfg.series_prefix == "QL-X" and cfg.pan == "AABCS1234A" and cfg.terms_list == ["T1", "T2"]
    client.get(reverse("invoices:new"), {"contract": setup["c"].id})
    with tenant_context(two_tenants["a"].id):
        inv = Invoice.objects.get(contract=setup["c"])
    client.post(reverse("invoices:issue", args=[inv.id]))
    with tenant_context(two_tenants["a"].id):
        inv.refresh_from_db()
        assert inv.number.startswith("QL-X/")
        csv_text = zoho_books_csv(Invoice.objects.all())
        assert "Invoice Number" in csv_text and inv.number in csv_text and "998713" in csv_text and "Telangana" in csv_text
        xml = tally_xml(Invoice.objects.all(), "A Co")
        assert xml.startswith("<?xml") and "Output CGST @9%" in xml and inv.number in xml and "56640.00" in xml
    r = client.get(reverse("invoices:export", args=["zoho"]))
    assert r.status_code == 200 and "text/csv" in r["Content-Type"]
    assert client.get(reverse("invoices:export", args=["tally"]))["Content-Type"].startswith("application/xml")
    assert client.get(reverse("reports:export", args=["ageing"])).status_code == 200
    assert client.get(reverse("reports:export", args=["invoices"])).status_code == 200
    # the contract page now links the invoice and no longer offers the banner
    body = client.get(reverse("contracts:detail", args=[setup["c"].id])).content.decode()
    assert inv.number in body and "No invoice yet" not in body
    # the manager (team) may not touch invoice settings
    client.logout()
    client = login(two_tenants["mgr_a"])
    assert client.post(reverse("invoices:settings"), {"legal_name": "x"}).status_code == 403
