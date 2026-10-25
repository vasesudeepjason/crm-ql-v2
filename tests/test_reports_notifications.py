from datetime import date, timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.approvals.models import Approval
from apps.core.models import AuditLog
from apps.core.tenancy import rls_bypass, tenant_context
from apps.crm.models import Lead
from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.services import follow_up_digest, stale_approvals_digest
from apps.reports.services import fy_start


# ---------- reports ----------

def test_fy_start():
    assert fy_start(date(2026, 10, 25)) == date(2026, 4, 1)
    assert fy_start(date(2027, 2, 1)) == date(2026, 4, 1)
    assert fy_start(date(2027, 4, 1)) == date(2027, 4, 1)


@pytest.mark.parametrize("tab", ["pipeline", "conversion", "team", "activity"])
def test_report_tabs_render_and_are_scoped(two_tenants, login, client, tab):
    with tenant_context(two_tenants["a"].id):
        Lead.objects.create(name="Won One", company=two_tenants["lead_a"].company, owner=two_tenants["tc_a"], stage="won", won_at=timezone.now(), est_value_paise=50000_00, source="Referral")
        Lead.objects.create(name="Lost One", owner=two_tenants["tc_a"], stage="lost", lost_reason="Budget", source="Referral")
    client = login(two_tenants["owner_a"])
    r = client.get(reverse("reports:index"), {"tab": tab, "period": "fy"})
    assert r.status_code == 200 and b"Lead B" not in r.content
    if tab == "conversion":
        assert b"Budget" in r.content and b"Referral" in r.content
    if tab == "team":
        assert b"Tc" in r.content
    # manager sees only their team; telecaller only themself; tenant B sees nothing of A
    client.logout()
    client = login(two_tenants["mgr_a"])
    r = client.get(reverse("reports:index"), {"tab": "team"})
    assert b"tc@a.test" in r.content and b"tc2@a.test" not in r.content
    client.logout()
    client = login(two_tenants["owner_b"])
    assert b"Won One" not in client.get(reverse("reports:index"), {"tab": "pipeline"}).content


def test_custom_period_and_owner_filter(two_tenants, login, client):
    client = login(two_tenants["owner_a"])
    r = client.get(reverse("reports:index"), {"tab": "conversion", "period": "custom", "from": "2026-01-01", "to": "2026-01-31", "owner": two_tenants["tc_a"].id})
    assert r.status_code == 200 and b"01 Jan 2026" in r.content


def test_exports_are_scoped_and_audited(two_tenants, login, client):
    client = login(two_tenants["owner_a"])  # managers have no data.export; owners/admins/accountants do
    r = client.get(reverse("reports:export", args=["leads"]))
    assert r.status_code == 200 and "text/csv" in r["Content-Type"]
    body = r.content.decode("utf-8-sig")
    assert "Lead A" in body and "Lead B" not in body
    with rls_bypass():
        assert AuditLog.objects.filter(action="export.leads", actor=two_tenants["owner_a"]).exists()
    for what in ("companies", "calls", "visits", "approvals"):
        assert client.get(reverse("reports:export", args=[what])).status_code == 200
    assert client.get(reverse("reports:export", args=["nope"])).status_code == 404
    client.logout()
    client = login(two_tenants["mgr_a"])
    assert client.get(reverse("reports:export", args=["leads"])).status_code == 403  # manager: reports yes, export no
    client.logout()
    client = login(two_tenants["tc_a"])  # no data.export
    assert client.get(reverse("reports:export", args=["leads"])).status_code == 403
    assert client.get(reverse("reports:index")).status_code == 403  # no reports.view


# ---------- notifications ----------

def test_submission_notifies_manager_and_decision_notifies_submitter(two_tenants, login, client):
    tc, mgr = two_tenants["tc_a"], two_tenants["mgr_a"]
    mail.outbox.clear()
    # the fixture already created an approval submitted by tc_a → manager should have been notified on creation
    with tenant_context(two_tenants["a"].id):
        n = Notification.objects.filter(user=mgr, kind="approval_submitted").first()
        assert n is not None and "Tc" in n.title and n.url.startswith("/approvals/")
    client = login(mgr)
    mail.outbox.clear()
    client.post(reverse("approvals:reject", args=[two_tenants["appr"].id]), {"reason": "Duplicate of an existing lead"})
    with tenant_context(two_tenants["a"].id):
        n = Notification.objects.filter(user=tc, kind="approval_decided").latest("created_at")
        assert "rejected" in n.title and n.body == "Duplicate of an existing lead" and n.emailed_at is not None
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [tc.email] and "Duplicate" in mail.outbox[0].body
    # bell shows unread, opening marks read
    client.logout()
    client = login(tc)
    r = client.get(reverse("dashboard:home"))
    assert b'title="1 unread"' in r.content or b'title="2 unread"' in r.content
    r = client.get(reverse("notifications:open", args=[n.id]))
    assert r.status_code == 302 and r.url == n.url
    with tenant_context(two_tenants["a"].id):
        n.refresh_from_db()
        assert n.is_read


def test_email_preference_off_still_creates_in_app(two_tenants, login, client):
    tc = two_tenants["tc_a"]
    NotificationPreference.objects.create(user=tc, email_approvals=False)
    client = login(two_tenants["owner_a"])
    mail.outbox.clear()
    client.post(reverse("approvals:approve", args=[two_tenants["appr"].id]))
    with tenant_context(two_tenants["a"].id):
        n = Notification.objects.filter(user=tc, kind="approval_decided").latest("created_at")
        assert "approved" in n.title and n.emailed_at is None
    assert mail.outbox == []


def test_follow_up_and_stale_digests(two_tenants):
    tc = two_tenants["tc_a"]
    today = timezone.localdate()
    with tenant_context(two_tenants["a"].id):
        Lead.objects.create(name="Due Today", owner=tc, stage="contacted", next_follow_up=today)
        Lead.objects.create(name="Overdue", owner=tc, stage="follow_up", next_follow_up=today - timedelta(days=3))
        Lead.objects.create(name="Future", owner=tc, stage="new", next_follow_up=today + timedelta(days=2))
        Approval.objects.filter(pk=two_tenants["appr"].pk).update(submitted_at=timezone.now() - timedelta(days=5))
    mail.outbox.clear()
    assert follow_up_digest(two_tenants["a"].id) == 1
    with tenant_context(two_tenants["a"].id):
        n = Notification.objects.get(user=tc, kind="follow_ups")
        assert n.title == "1 follow-up due today, 1 overdue" and "Overdue" in n.body and "Future" not in n.body and n.url == "/telecaller/"
    assert stale_approvals_digest(two_tenants["a"].id) >= 1
    with tenant_context(two_tenants["a"].id):
        assert Notification.objects.filter(user=two_tenants["mgr_a"], kind="stale_approvals").exists()
    assert any("follow-up" in m.subject for m in mail.outbox) and any("waiting more than" in m.subject for m in mail.outbox)
    # running the command again today does not spam a second follow-up mail per user? (it does send again by design; just check it runs)
    call_command("daily_jobs", tenant=two_tenants["a"].id)


def test_prefs_form_and_notifications_page(two_tenants, login, client):
    client = login(two_tenants["tc_a"])
    r = client.post(reverse("notifications:prefs"), {"email_reminders": "on"})
    assert r.status_code == 302
    p = NotificationPreference.objects.get(user=two_tenants["tc_a"])
    assert p.email_approvals is False and p.email_reminders is True
    r = client.get(reverse("notifications:index"))
    assert r.status_code == 200
