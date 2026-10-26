from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Membership
from apps.contracts.models import Contract, Product
from apps.contracts.services import renewal_board, roll_auto_renewals, send_renewal_reminders
from apps.core.tenancy import tenant_context
from apps.crm.models import Contact, Lead
from apps.notifications.models import Notification
from apps.tasks.models import Task


@pytest.fixture
def book(two_tenants):
    """A catalogue item and four contracts in tenant A at different distances from expiry, one in tenant B."""
    today = timezone.localdate()
    owner, mgr, tc = two_tenants["owner_a"], two_tenants["mgr_a"], two_tenants["tc_a"]
    co = two_tenants["lead_a"].company
    with tenant_context(two_tenants["a"].id):
        p = Product(name="AMC Gold", hsn_sac="998713", gst_rate=Decimal("18"), default_price_paise=48000_00)
        p.created_by = owner
        p.save()
        contact = Contact.objects.create(company=co, name="Priya", email="priya@acustomer.in", is_primary=True)

        def mk(off, **kw):
            c = Contract(company=co, product=p, start_date=today + timedelta(days=off - 364), end_date=today + timedelta(days=off), amount_paise=48000_00, owner=kw.pop("owner", owner), **kw)
            c.created_by = owner
            c.save()
            return c

        c5 = mk(5, contact=contact, contact_email="priya@acustomer.in")
        c45 = mk(45, owner=mgr)
        c200 = mk(200, owner=mgr)
        expired = mk(-2, auto_renew=True)
    with tenant_context(two_tenants["b"].id):
        pb = Product(name="B Plan", default_price_paise=100)
        pb.created_by = two_tenants["owner_b"]
        pb.save()
        cb = Contract(company=two_tenants["lead_b"].company, product=pb, start_date=today, end_date=today + timedelta(days=10), amount_paise=100, owner=two_tenants["owner_b"])
        cb.created_by = two_tenants["owner_b"]
        cb.save()
    return dict(p=p, c5=c5, c45=c45, c200=c200, expired=expired, cb=cb, today=today, co=co)


def test_numbering_and_derived_values(two_tenants, book):
    with tenant_context(two_tenants["a"].id):
        c = book["c5"]
        assert c.number.startswith("CT/") and c.number.endswith("/0001")
        assert book["c45"].number.endswith("/0002")
        assert c.gst_paise == 8640_00 and c.total_paise == 56640_00 and c.annual_value_paise == 48000_00
        assert c.bucket == "7" and book["c45"].bucket == "60" and book["expired"].bucket == "expired"
        monthly = Contract(company=book["co"], product=book["p"], start_date=book["today"], end_date=book["today"] + timedelta(days=364), billing_cycle="monthly", amount_paise=3500_00)
        assert monthly.annual_value_paise == 42000_00


def test_list_tracker_scope_and_tenant_isolation(two_tenants, book, login, client):
    client = login(two_tenants["owner_a"])
    r = client.get(reverse("contracts:index"))
    assert r.status_code == 200 and b"A Customer" in r.content and b"B Customer" not in r.content
    r = client.get(reverse("contracts:renewals"))
    body = r.content.decode()
    assert "3 contracts end within 90 days" in body and "Expired" in body and "Next 7 days" in body
    assert client.get(reverse("contracts:index"), {"status": "active", "due": "30"}).content.count(b"CT/") == 1  # c5 only; expired has its own filter
    assert client.get(reverse("contracts:index"), {"status": "active", "due": "expired"}).content.count(b"CT/") == 1
    client.logout()
    # manager (team scope) sees only their own + team's; the owner's contracts are not theirs
    client = login(two_tenants["mgr_a"])
    body = client.get(reverse("contracts:index"), {"status": "all"}).content.decode()
    assert body.count("CT/") == 2
    client.logout()
    client = login(two_tenants["owner_b"])
    assert client.get(reverse("contracts:detail", args=[book["c5"].id])).status_code == 404
    client.logout()
    client = login(two_tenants["tc_a"])  # telecaller: no contracts.view
    assert client.get(reverse("contracts:index")).status_code == 403


def test_create_contract_from_won_lead_and_edit(two_tenants, book, login, client):
    lead = two_tenants["lead_a"]
    with tenant_context(two_tenants["a"].id):
        lead.move_to("won", two_tenants["owner_a"])
    client = login(two_tenants["owner_a"])
    body = client.get(reverse("crm:lead_detail", args=[lead.id])).content.decode()
    assert "Create contract" in body
    r = client.get(reverse("contracts:new"), {"lead": lead.id})
    assert r.status_code == 200 and b"Renewal owner" in r.content
    today = book["today"]
    r = client.post(reverse("contracts:new"), {"company": lead.company_id, "product": book["p"].id, "start_date": today, "end_date": today + timedelta(days=364), "billing_cycle": "yearly", "amount": "52000", "owner": two_tenants["owner_a"].id, "lead": lead.id, "contact": "", "contact_email": "", "notes": "3 depots"})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        c = Contract.objects.get(lead=lead)
        assert c.amount_paise == 52000_00 and c.number.startswith("CT/26-27/") and Contract.objects.filter(number=c.number).count() == 1
    body = client.get(reverse("crm:lead_detail", args=[lead.id])).content.decode()
    assert "Create contract" not in body and c.number in body
    # end before start is rejected
    r = client.post(reverse("contracts:edit", args=[c.id]), {"company": lead.company_id, "product": book["p"].id, "start_date": today, "end_date": today - timedelta(days=1), "billing_cycle": "yearly", "amount": "52000", "owner": two_tenants["owner_a"].id})
    assert r.status_code == 200 and b"before the start date" in r.content
    # the manager may not edit the owner's contract
    client.logout()
    client = login(two_tenants["mgr_a"])
    assert client.get(reverse("contracts:edit", args=[c.id])).status_code == 404


def test_renew_and_lapse_and_win_back(two_tenants, book, login, client):
    client = login(two_tenants["owner_a"])
    c = book["c5"]
    r = client.get(reverse("contracts:renew", args=[c.id]))
    assert r.status_code == 200 and b"Create the new term" in r.content
    s, e = c.next_term()
    r = client.post(reverse("contracts:renew", args=[c.id]), {"start_date": s, "end_date": e, "amount": "51000", "auto_renew": "on", "notes": "8% up"})
    assert r.status_code == 204 and r["HX-Redirect"].startswith("/contracts/")
    with tenant_context(two_tenants["a"].id):
        c.refresh_from_db()
        new = c.renewed_by
        assert c.status == "renewed" and new.renewal_of == c and new.amount_paise == 51000_00 and new.auto_renew and new.start_date == s and new.contact_email == "priya@acustomer.in"
    body = client.get(reverse("contracts:detail", args=[new.id])).content.decode()
    assert "Renewal history" in body and c.number in body
    # lapse the 45-day one (owned by the manager; owner has all scope)
    c45 = book["c45"]
    assert client.get(reverse("contracts:lapse", args=[c45.id])).status_code == 200
    assert client.post(reverse("contracts:lapse", args=[c45.id]), {"reason": ""}).status_code == 422
    assert client.post(reverse("contracts:lapse", args=[c45.id]), {"reason": "Went with a local vendor"}).status_code == 204
    with tenant_context(two_tenants["a"].id):
        c45.refresh_from_db()
        assert c45.status == "lapsed" and c45.lapsed_reason == "Went with a local vendor"
    body = client.get(reverse("contracts:renewals")).content.decode()
    assert "win-back" in body and "Went with a local vendor" in body
    # win back: renew from lapsed keeps the old one lapsed but links the new term
    s, e = c45.next_term()
    assert client.post(reverse("contracts:renew", args=[c45.id]), {"start_date": s, "end_date": e, "amount": "48000"}).status_code == 204
    with tenant_context(two_tenants["a"].id):
        c45.refresh_from_db()
        assert c45.status == "lapsed" and c45.renewed_by is not None
    # a renewed contract cannot be lapsed
    assert client.get(reverse("contracts:lapse", args=[c.id])).status_code == 403


def test_reminders_owner_and_client_idempotent(two_tenants, book):
    mail.outbox.clear()
    sent = send_renewal_reminders(two_tenants["a"].id, book["today"])
    # c5 → owner (7 threshold) + client (30 and 7); c45 → owner (60); expired → owner; c200 → nothing
    assert sent == {"owner": 3, "client": 1}
    with tenant_context(two_tenants["a"].id):
        c5 = Contract.objects.get(pk=book["c5"].pk)
        assert c5.reminders == {"owner": [90, 60, 30, 7], "client": [30, 7]}
        assert Contract.objects.get(pk=book["c45"].pk).reminders == {"owner": [90, 60], "client": []}
        assert Notification.objects.filter(user=two_tenants["owner_a"], kind="contract_renewal").count() == 2
        n = Notification.objects.filter(user=two_tenants["owner_a"], kind="contract_renewal", title__contains="expires in 5 days").first()
        assert n is not None and n.url == f"/contracts/{c5.pk}/"
    client_mails = [m for m in mail.outbox if m.to == ["priya@acustomer.in"]]
    assert len(client_mails) == 1 and "in 5 days" in client_mails[0].subject and "Dear Priya" in client_mails[0].body
    # second run the same day sends nothing new
    mail.outbox.clear()
    assert send_renewal_reminders(two_tenants["a"].id, book["today"]) == {"owner": 0, "client": 0}
    assert mail.outbox == []
    # later, crossing the next threshold sends again
    assert send_renewal_reminders(two_tenants["a"].id, book["today"] + timedelta(days=20)) == {"owner": 1, "client": 0}  # c45 reaches 30


def test_auto_renew_roll_over_and_daily_jobs(two_tenants, book):
    assert roll_auto_renewals(two_tenants["a"].id, book["today"]) == 1
    with tenant_context(two_tenants["a"].id):
        old = Contract.objects.get(pk=book["expired"].pk)
        new = old.renewed_by
        assert old.status == "renewed" and new.start_date == old.end_date + timedelta(days=1) and new.term_days == old.term_days and new.auto_renew
        assert Notification.objects.filter(user=two_tenants["owner_a"], title__startswith="Auto-renewed").exists()
    assert roll_auto_renewals(two_tenants["a"].id, book["today"]) == 0
    call_command("daily_jobs", tenant=two_tenants["a"].id)


def test_renewal_board_math(two_tenants, book):
    with tenant_context(two_tenants["a"].id):
        b = renewal_board(Contract.objects.all(), book["today"])
    assert b["expired"]["n"] == 1 and b["7"]["n"] == 1 and b["60"]["n"] == 1 and b["30"]["n"] == 0 and b["total_n"] == 3
    assert b["total_risk"] == 3 * 48000_00


def test_catalogue_permissions_and_validation(two_tenants, login, client):
    client = login(two_tenants["mgr_a"])  # manager: team scope on contracts.edit → may manage
    assert client.get(reverse("contracts:products")).status_code == 200
    r = client.post(reverse("contracts:product_new"), {"name": "AMC Silver", "kind": "service", "hsn_sac": "99871", "gst_rate": "18", "default_cycle": "yearly", "price": "24000", "is_active": "on"})
    assert r.status_code == 422 and b"4, 6 or 8 digits" in r.content
    r = client.post(reverse("contracts:product_new"), {"name": "AMC Silver", "kind": "service", "hsn_sac": "998713", "gst_rate": "18", "default_cycle": "yearly", "price": "24000", "is_active": "on"})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        p = Product.objects.get(name="AMC Silver")
        assert p.default_price_paise == 24000_00 and p.gst_rate == Decimal("18")
    client.logout()
    # a sales executive (own scope) sees the catalogue but may not edit it
    sales_m = Membership.objects.get(user=two_tenants["other_tc"])
    sales_m.role = "sales"
    sales_m.save()
    client = login(two_tenants["other_tc"])
    assert client.get(reverse("contracts:products")).status_code == 200
    assert client.get(reverse("contracts:product_new")).status_code == 403
    assert client.get(reverse("contracts:index")).status_code == 200


def test_task_linked_to_contract_and_export(two_tenants, book, login, client):
    client = login(two_tenants["owner_a"])
    c = book["c5"]
    r = client.post(reverse("tasks:new"), {"title": "Renewal call", "assignee": two_tenants["owner_a"].id, "status": "todo", "priority": "high", "contract": c.id})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        t = Task.objects.get(title="Renewal call")
        assert t.contract == c and t.company == c.company
    assert b"Renewal call" in client.get(reverse("contracts:detail", args=[c.id])).content
    r = client.get(reverse("reports:export", args=["contracts"]))
    assert r.status_code == 200 and "A Customer" in r.content.decode("utf-8-sig") and "B Customer" not in r.content.decode("utf-8-sig")
