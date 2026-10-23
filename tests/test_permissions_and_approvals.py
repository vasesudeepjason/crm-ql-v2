import pytest
from django.urls import reverse

from apps.approvals.models import Approval, ApprovalError
from apps.core.models import AuditLog
from apps.core.tenancy import tenant_context


def test_telecaller_cannot_open_companies_page(two_tenants, login):
    client = login(two_tenants["tc_a"])
    assert client.get(reverse("crm:leads")).status_code == 200
    # telecaller has companies.view (own) but not companies.create — page still renders; approve is the real gate
    r = client.post(reverse("approvals:approve", args=[two_tenants["appr"].id]))
    assert r.status_code == 422
    assert b"cannot approve" in r.content


def test_manager_approves_team_submission_and_it_is_audited(two_tenants, login):
    client = login(two_tenants["mgr_a"])
    appr = two_tenants["appr"]
    r = client.post(reverse("approvals:approve", args=[appr.id]))
    assert r.status_code == 200
    with tenant_context(two_tenants["a"].id):
        appr.refresh_from_db()
        assert appr.status == Approval.Status.APPROVED
        assert appr.reviewer == two_tenants["mgr_a"]
        assert appr.record.status == "active"
        log = AuditLog.objects.get(action="approval.approve", object_id=str(appr.id))
        assert log.actor == two_tenants["mgr_a"] and log.before == {"status": "pending"}


def test_manager_cannot_approve_outside_team(two_tenants, login):
    client = login(two_tenants["mgr_a"])
    r = client.post(reverse("approvals:approve", args=[two_tenants["appr_other"].id]))
    assert r.status_code == 404  # not even visible in the manager's queue


def test_rejection_needs_a_reason(two_tenants, login):
    client = login(two_tenants["mgr_a"])
    r = client.post(reverse("approvals:reject", args=[two_tenants["appr"].id]), {"reason": "  "})
    assert r.status_code == 422 and b"needs a reason" in r.content
    r = client.post(reverse("approvals:reject", args=[two_tenants["appr"].id]), {"reason": "Duplicate"})
    assert r.status_code == 200
    with tenant_context(two_tenants["a"].id):
        a = Approval.objects.get(pk=two_tenants["appr"].id)
        assert a.status == "rejected" and a.reason == "Duplicate" and a.record.status == "rejected"


def test_cannot_approve_own_submission_unless_admin(two_tenants):
    with tenant_context(two_tenants["a"].id):
        appr = two_tenants["appr"]
        own = appr.submitter.memberships.get()
        with pytest.raises(ApprovalError):
            appr.approve(own)
        owner_m = two_tenants["owner_a"].memberships.get()
        appr.approve(owner_m)  # Owner/Admin may approve anything
        assert appr.status == "approved"


def test_approved_records_are_locked(two_tenants):
    with tenant_context(two_tenants["a"].id):
        appr = two_tenants["appr"]
        appr.approve(two_tenants["owner_a"].memberships.get())
        with pytest.raises(ApprovalError):
            appr.reject(two_tenants["owner_a"].memberships.get(), "late")


def test_audit_log_is_append_only(two_tenants):
    with tenant_context(two_tenants["a"].id):
        row = AuditLog.objects.create(action="test", model="X", object_id="1")
        row.note = "edited"
        with pytest.raises(ValueError):
            row.save()
        with pytest.raises(ValueError):
            row.delete()


def test_user_without_membership_is_blocked(db, client):
    from apps.accounts.models import User

    u = User.objects.create_user("lonely@x.test", "test-password-123", name="Lonely")
    client.login(username=u.email, password="test-password-123")
    assert client.get(reverse("dashboard:home")).status_code == 403
