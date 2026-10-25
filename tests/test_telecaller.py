import zoneinfo
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.approvals.models import Approval
from apps.core.models import AuditLog
from apps.core.tenancy import rls_bypass, tenant_context
from apps.crm.models import Activity, Company, Lead
from apps.telecaller.models import CallTarget
from apps.telecaller.services import call_queue, today_progress


def test_queue_orders_overdue_due_new_then_rest(two_tenants):
    tc = two_tenants["tc_a"]
    today = timezone.localdate()
    with tenant_context(two_tenants["a"].id):
        Lead.objects.filter(pk=two_tenants["lead_a"].pk).update(next_follow_up=today + timedelta(days=5), stage="contacted")
        overdue = Lead.objects.create(name="Overdue", phone="+91 9000000001", owner=tc, stage="follow_up", next_follow_up=today - timedelta(days=2))
        due = Lead.objects.create(name="Due", phone="+91 9000000002", owner=tc, stage="contacted", next_follow_up=today)
        fresh = Lead.objects.create(name="Fresh", phone="+91 9000000003", owner=tc, stage="new")
        Lead.objects.create(name="DNC", phone="+91 9000000004", owner=tc, stage="new", do_not_contact=True)
        Lead.objects.create(name="Pending", phone="+91 9000000005", owner=tc, stage="new", status="pending")
        q = call_queue(tc)
    names = [l.name for l in q]
    assert names[:3] == ["Overdue", "Due", "Fresh"] and "Lead A" in names and "DNC" not in names and "Pending" not in names
    assert q[0].reason == "overdue" and q[1].reason == "due today" and "never called" in q[2].reason


def test_desk_renders_and_quick_log_updates_lead(two_tenants, login, client):
    client = login(two_tenants["tc_a"])
    r = client.get(reverse("telecaller:desk"))
    assert r.status_code == 200 and b"Next to call" in r.content and b"Lead A" in r.content
    lead = two_tenants["lead_a"]
    r = client.get(reverse("telecaller:log_form", args=[lead.id]))
    assert r.status_code == 200 and b"Save call" in r.content
    r = client.post(reverse("telecaller:log_call", args=[lead.id]), {"outcome": "", "notes": "x"})
    assert r.status_code == 422
    r = client.post(reverse("telecaller:log_call", args=[lead.id]), {"outcome": "interested", "duration_minutes": "3", "notes": "Wants brochure", "next_follow_up": "2026-11-03"})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        lead.refresh_from_db()
        a = Activity.objects.get(lead=lead, type="call")
        assert a.duration_seconds == 180 and a.created_by == two_tenants["tc_a"]
        assert lead.stage == "interested" and str(lead.next_follow_up) == "2026-11-03"
        assert AuditLog.objects.filter(action="call.log").exists()


def test_no_answer_sets_tomorrow_and_wrong_number_flags_dnc(two_tenants, login, client):
    client = login(two_tenants["tc_a"])
    lead = two_tenants["lead_a"]
    client.post(reverse("telecaller:log_call", args=[lead.id]), {"outcome": "no_answer"})
    with tenant_context(two_tenants["a"].id):
        lead.refresh_from_db()
        ist_today = timezone.now().astimezone(zoneinfo.ZoneInfo("Asia/Kolkata")).date()  # requests run in the tenant zone
        assert lead.next_follow_up == ist_today + timedelta(days=1) and lead.stage == "attempted"
    client.post(reverse("telecaller:log_call", args=[lead.id]), {"outcome": "wrong_number"})
    with tenant_context(two_tenants["a"].id):
        lead.refresh_from_db()
        assert lead.do_not_contact is True


def test_cannot_log_on_someone_elses_lead(two_tenants, login, client):
    client = login(two_tenants["other_tc"])
    assert client.get(reverse("telecaller:log_form", args=[two_tenants["lead_a"].id])).status_code == 404
    assert client.post(reverse("telecaller:log_call", args=[two_tenants["lead_a"].id]), {"outcome": "connected"}).status_code == 404


def test_add_lead_from_call_logs_first_call_and_goes_to_approval(two_tenants, login, client):
    client = login(two_tenants["tc_a"])
    r = client.get(reverse("crm:lead_create") + "?from_call=1&phone=9876500001")
    assert r.status_code == 200 and b"Log this call" in r.content and b"9876500001" in r.content
    r = client.post(reverse("crm:lead_create"), {"name": "Caller Person", "phone": "9876500001", "stage": "new", "source": "Telecalling", "owner": two_tenants["tc_a"].id, "first_call_outcome": "interested", "first_call_notes": "Asked for pricing", "consent_note": "ok"})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        lead = Lead.objects.get(name="Caller Person")
        assert lead.status == "pending"
        assert Activity.objects.filter(lead=lead, type="call", outcome="interested", notes="Asked for pricing").exists()
        ap = Approval.objects.get(kind="lead", object_id=lead.id)
        assert ap.summary["outcome"] == "Interested" and ap.summary["note"] == "Asked for pricing"


def test_targets_progress_and_manager_sets_them(two_tenants, login, client):
    tc, mgr = two_tenants["tc_a"], two_tenants["mgr_a"]
    client = login(mgr)
    r = client.get(reverse("telecaller:team"))
    assert r.status_code == 200 and b"Tc" in r.content and b"no target" in r.content
    r = client.post(reverse("telecaller:set_target", args=[tc.id]), {"calls_per_day": "20", "leads_per_day": "2"})
    assert r.status_code == 302
    t = CallTarget.all_objects.get(user=tc)
    assert t.calls_per_day == 20 and t.set_by == mgr
    # out-of-team telecaller cannot be targeted by this manager
    assert client.post(reverse("telecaller:set_target", args=[two_tenants["other_tc"].id]), {"calls_per_day": "1", "leads_per_day": "1"}).status_code == 403
    client.logout()
    client = login(tc)
    assert client.get(reverse("telecaller:team")).status_code == 403
    assert client.post(reverse("telecaller:set_target", args=[tc.id]), {"calls_per_day": "999", "leads_per_day": "9"}).status_code == 403
    for i in range(5):
        client.post(reverse("telecaller:log_call", args=[two_tenants["lead_a"].id]), {"outcome": "connected" if i % 2 else "busy"})
    with tenant_context(two_tenants["a"].id):
        p = today_progress(tc)
    assert p["calls"] == 5 and p["target_calls"] == 20 and p["calls_pct"] == 25 and p["connected"] == 2
    r = client.get(reverse("dashboard:home"))
    assert b"Today's targets" in r.content and b"5 / 20" in r.content
    r = client.get(reverse("telecaller:desk"))
    assert b"TARGET 20" in r.content


def test_team_page_is_tenant_and_team_scoped(two_tenants, login, client):
    client = login(two_tenants["owner_b"])
    r = client.get(reverse("telecaller:team"))
    assert r.status_code == 200 and b"tc@a.test" not in r.content
