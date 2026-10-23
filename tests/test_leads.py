import json
from datetime import date

import pytest
from django.urls import reverse

from apps.approvals.models import Approval
from apps.core.models import AuditLog
from apps.core.tenancy import rls_bypass, tenant_context
from apps.crm.duplicates import find_duplicates
from apps.crm.models import Activity, Company, Lead, LeadStageHistory

BASE = {"name": "Priya Sharma", "phone": "98765 43210", "email": "", "stage": "new", "source": "Referral", "est_value": "48000", "consent_note": "Agreed to follow-up on call"}


def test_owner_creates_active_lead_with_new_company_and_history(two_tenants, login, client):
    login(two_tenants["owner_a"])
    r = client.post(reverse("crm:lead_create"), {**BASE, "new_company": "Omega Diagnostics", "owner": two_tenants["tc_a"].id, "tags_text": "AMC, Priority"})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        lead = Lead.objects.get(name="Priya Sharma")
        assert lead.status == "active" and lead.owner == two_tenants["tc_a"] and lead.est_value_paise == 48000_00 and lead.tags == ["AMC", "Priority"]
        assert lead.company.name == "Omega Diagnostics" and lead.phone_normalized == "+919876543210"
        assert lead.history.get().to_stage == "new"
        assert not Approval.objects.filter(object_id=lead.id).exists()
    assert client.get(r.url).status_code == 200


def test_telecaller_lead_goes_to_approval_queue(two_tenants, login, client):
    login(two_tenants["tc_a"])
    r = client.post(reverse("crm:lead_create"), {**BASE, "owner": two_tenants["tc_a"].id})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        lead = Lead.objects.get(name="Priya Sharma")
        assert lead.status == "pending" and lead.owner == two_tenants["tc_a"]
        ap = Approval.objects.get(object_id=lead.id, kind="lead")
        assert ap.submitter == two_tenants["tc_a"] and ap.status == "pending" and ap.value_paise == 48000_00
    # the telecaller can still edit their own pending lead, but not another telecaller's
    assert client.get(reverse("crm:lead_edit", args=[lead.id])).status_code == 200
    assert client.get(reverse("crm:lead_edit", args=[two_tenants["lead_a"].id])).status_code in (403, 404)


def test_duplicate_check_blocks_unless_overridden(two_tenants, login, client):
    login(two_tenants["owner_a"])
    with tenant_context(two_tenants["a"].id):
        Lead.objects.create(name="Existing Person", phone="+91 98765 43210", owner=two_tenants["owner_a"])
    # live check endpoint
    r = client.get(reverse("crm:check_duplicate"), {"phone": "9876543210", "name": ""})
    assert r.status_code == 200 and b"Existing Person" in r.content and b"same phone" in r.content
    r = client.get(reverse("crm:check_duplicate"), {"name": "Existing Persn"})
    assert b"similar name" in r.content
    # submit without override → not created, warning shown
    r = client.post(reverse("crm:lead_create"), BASE)
    assert r.status_code == 200 and b"Existing Person" in r.content
    with tenant_context(two_tenants["a"].id):
        assert not Lead.objects.filter(name="Priya Sharma").exists()
    r = client.post(reverse("crm:lead_create"), {**BASE, "create_anyway": "on"})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        assert Lead.objects.filter(name="Priya Sharma").exists()
        assert AuditLog.objects.get(action="lead.create", object_id=str(Lead.objects.get(name="Priya Sharma").id)).after["duplicates_overridden"] == 1


def test_duplicates_are_tenant_scoped(two_tenants):
    with tenant_context(two_tenants["b"].id):
        Lead.objects.create(name="B Person", phone="+91 91111 11111", owner=two_tenants["owner_b"])
    with tenant_context(two_tenants["a"].id):
        assert find_duplicates(phone="9111111111") == []


def test_form_validation(two_tenants, login, client):
    login(two_tenants["owner_a"])
    r = client.post(reverse("crm:lead_create"), {**BASE, "phone": "", "email": ""})
    assert b"at least a phone number or an email" in r.content
    r = client.post(reverse("crm:lead_create"), {**BASE, "phone": "1"})
    assert b"valid phone" in r.content


def test_stage_change_rules_and_history(two_tenants, login, client):
    login(two_tenants["owner_a"])
    lead = two_tenants["lead_a"]
    r = client.post(reverse("crm:lead_stage", args=[lead.id]), {"stage": "lost"})
    assert r.status_code == 422 and b"needs a reason" in r.content
    r = client.post(reverse("crm:lead_stage", args=[lead.id]), {"stage": "qualified", "note": "budget confirmed", "next_follow_up": "2026-11-05"})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        lead.refresh_from_db()
        assert lead.stage == "qualified" and lead.next_follow_up == date(2026, 11, 5)
        h = LeadStageHistory.objects.filter(lead=lead).order_by("-created_at").first()
        assert h.from_stage == "new" and h.to_stage == "qualified" and h.note == "budget confirmed"
        # Won needs a company: lead_a has one, so this works and stamps won_at
        lead.company = None
        lead.save()
    r = client.post(reverse("crm:lead_stage", args=[lead.id]), {"stage": "won"})
    assert r.status_code == 422 and b"company attached" in r.content
    with tenant_context(two_tenants["a"].id):
        lead.company = Company.objects.get(name="A Customer")
        lead.save()
    r = client.post(reverse("crm:lead_stage", args=[lead.id]), {"stage": "won"})
    with tenant_context(two_tenants["a"].id):
        lead.refresh_from_db()
        assert lead.stage == "won" and lead.won_at is not None


def test_activity_logging_moves_stage_and_shows_on_timeline(two_tenants, login, client):
    login(two_tenants["tc_a"])  # telecaller, calls.create own
    lead = two_tenants["lead_a"]
    r = client.post(reverse("crm:activity_add", args=[lead.id]), {"type": "call", "outcome": "", "notes": "x"})
    assert r.status_code == 422 and b"call outcome" in r.content
    r = client.post(reverse("crm:activity_add", args=[lead.id]), {"type": "call", "outcome": "interested", "duration_minutes": "4", "notes": "Wants a demo", "next_follow_up": "2026-11-02"})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        a = Activity.objects.get(lead=lead, type="call")
        assert a.duration_seconds == 240 and a.created_by == two_tenants["tc_a"]
        lead.refresh_from_db()
        assert lead.stage == "interested" and lead.next_follow_up == date(2026, 11, 2)
    r = client.get(reverse("crm:lead_detail", args=[lead.id]))
    assert r.status_code == 200 and b"Wants a demo" in r.content and b"Stage new" in r.content


def test_detail_and_edit_are_scoped(two_tenants, login, client):
    login(two_tenants["owner_b"])
    assert client.get(reverse("crm:lead_detail", args=[two_tenants["lead_a"].id])).status_code == 404
    client.logout()
    login(two_tenants["other_tc"])  # telecaller not owning lead_a
    assert client.get(reverse("crm:lead_detail", args=[two_tenants["lead_a"].id])).status_code == 404
    client.logout()
    login(two_tenants["mgr_a"])  # manager: team scope includes tc_a's lead
    assert client.get(reverse("crm:lead_detail", args=[two_tenants["lead_a"].id])).status_code == 200
    r = client.post(reverse("crm:lead_edit", args=[two_tenants["lead_a"].id]), {**BASE, "name": "Lead A renamed", "company": "", "owner": two_tenants["tc_a"].id})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        assert Lead.objects.get(pk=two_tenants["lead_a"].id).name == "Lead A renamed"
        assert AuditLog.objects.filter(action="lead.edit").exists()


def test_kanban_renders_and_moves(two_tenants, login, client):
    login(two_tenants["owner_a"])
    r = client.get(reverse("crm:kanban"))
    assert r.status_code == 200 and b"Lead A" in r.content and b'data-stage="won"' in r.content
    lead = two_tenants["lead_a"]
    r = client.post(reverse("crm:kanban_move", args=[lead.id]), {"stage": "lost"})
    assert r.status_code == 422 and json.loads(r.content)["needs_reason"]
    r = client.post(reverse("crm:kanban_move", args=[lead.id]), {"stage": "proposal"})
    assert r.status_code == 200 and json.loads(r.content)["ok"]
    with tenant_context(two_tenants["a"].id):
        lead.refresh_from_db()
        assert lead.stage == "proposal"
        assert LeadStageHistory.objects.filter(lead=lead, to_stage="proposal", note="kanban").exists()
    # a telecaller (no leads.edit) cannot move an active lead
    client.logout()
    login(two_tenants["tc_a"])
    assert client.post(reverse("crm:kanban_move", args=[lead.id]), {"stage": "new"}).status_code == 403
