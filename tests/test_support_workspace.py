import io
import zipfile
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.core.tenancy import rls_bypass
from apps.tenants.models import SupportSession, Tenant

from .conftest import PW


@pytest.fixture
def staff(db):
    return User.objects.create_user("support@quantumloop.in", PW, name="Sam Support", is_staff=True)


def test_staff_without_session_cannot_see_any_tenant(two_tenants, staff, client):
    client.login(username=staff.email, password=PW)
    r = client.get(reverse("dashboard:home"))
    assert r.status_code == 302 and r.url == "/platform/"
    assert client.get(reverse("tenants:support")).status_code == 200


def test_support_session_grants_admin_access_and_is_logged_and_visible(two_tenants, staff, client, login):
    client.login(username=staff.email, password=PW)
    r = client.post(reverse("tenants:support_start", args=[two_tenants["a"].id]), {"reason": "Ticket #482 duplicate leads", "minutes": "30"})
    assert r.status_code == 302 and r.url == reverse("dashboard:home")
    ss = SupportSession.objects.get()
    assert ss.tenant == two_tenants["a"] and ss.is_active and (ss.expires_at - ss.started_at) <= timedelta(minutes=31)

    # inside: sees tenant A only, acts as admin, banner shows, every action tagged
    r = client.get(reverse("crm:leads"))
    assert r.status_code == 200 and b"Lead A" in r.content and b"Lead B" not in r.content
    r = client.get(reverse("dashboard:home"))
    assert b"support session" in r.content.lower()
    r = client.post(reverse("approvals:approve", args=[two_tenants["appr"].id]))
    assert r.status_code == 200
    with rls_bypass():
        assert AuditLog.objects.filter(tenant=two_tenants["a"], action="support.start", actor=staff).exists()
        log = AuditLog.objects.get(action="approval.approve")
        assert log.actor == staff and f"support session #{ss.pk}" in log.note

    # tenant B stays closed
    assert client.get(reverse("approvals:detail", args=[two_tenants["appr"].id])).status_code == 200
    # leave
    client.post(reverse("tenants:support_leave"))
    ss.refresh_from_db()
    assert not ss.is_active and ss.end_reason == "left"
    assert client.get(reverse("dashboard:home")).status_code == 302  # back to platform only

    # the tenant owner sees the session on the workspace page and in the audit log
    client.logout()
    login(two_tenants["owner_a"])
    r = client.get(reverse("tenants:workspace"))
    assert b"Sam Support" in r.content and b"Ticket #482" in r.content
    r = client.get(reverse("core:audit"), {"group": "support"})
    assert b"support.start" in r.content and b"support.end" in r.content


def test_expired_session_is_closed_and_logged(two_tenants, staff, client):
    client.login(username=staff.email, password=PW)
    ss = SupportSession.objects.create(tenant=two_tenants["a"], staff=staff, reason="old", expires_at=timezone.now() - timedelta(minutes=1))
    s = client.session
    s["support_session_id"] = ss.pk
    s.save()
    assert client.get(reverse("dashboard:home")).status_code == 302
    ss.refresh_from_db()
    assert ss.end_reason == "expired"
    with rls_bypass():
        assert AuditLog.objects.filter(action="support.expire", object_id=str(ss.pk)).exists()


def test_tenant_admin_can_revoke_an_active_session(two_tenants, staff, client, login):
    ss = SupportSession.objects.create(tenant=two_tenants["a"], staff=staff, reason="checking", expires_at=timezone.now() + timedelta(hours=1))
    login(two_tenants["owner_a"])
    r = client.post(reverse("tenants:revoke_support", args=[ss.pk]))
    assert r.status_code == 302
    ss.refresh_from_db()
    assert ss.end_reason == "revoked" and ss.ended_by == two_tenants["owner_a"]
    # the staff member is bounced out on their next request
    client.logout()
    client.login(username=staff.email, password=PW)
    s = client.session
    s["support_session_id"] = ss.pk
    s.save()
    assert client.get(reverse("crm:leads")).status_code == 302
    # tenant B's owner cannot touch tenant A's session
    client.logout()
    login(two_tenants["owner_b"])
    assert client.post(reverse("tenants:revoke_support", args=[ss.pk])).status_code == 404


def test_support_start_needs_reason_and_staff(two_tenants, staff, client, login):
    login(two_tenants["owner_a"])
    assert client.post(reverse("tenants:support_start", args=[two_tenants["b"].id]), {"reason": "sneaky"}).status_code == 302  # redirected to admin login
    assert not SupportSession.objects.exists()
    client.logout()
    client.login(username=staff.email, password=PW)
    client.post(reverse("tenants:support_start", args=[two_tenants["a"].id]), {"reason": "x"})
    assert not SupportSession.objects.exists()


def test_workspace_edit_is_validated_and_audited(two_tenants, client, login):
    login(two_tenants["owner_a"])
    r = client.post(reverse("tenants:workspace"), {"name": "A Co Pvt Ltd", "timezone": "Asia/Dubai", "gstin": "27AAPFU0939F1ZW", "address": "x"})
    t = Tenant.objects.get(pk=two_tenants["a"].pk)
    assert t.name == "A Co" and t.gstin == ""  # bad checksum rejected, nothing saved
    r = client.post(reverse("tenants:workspace"), {"name": "A Co Pvt Ltd", "timezone": "Asia/Dubai", "gstin": "27aapfu0939f1zv", "address": "12 MG Road"})
    t.refresh_from_db()
    assert t.name == "A Co Pvt Ltd" and t.timezone == "Asia/Dubai" and t.gstin == "27AAPFU0939F1ZV"
    with rls_bypass():
        log = AuditLog.objects.get(action="workspace.update")
        assert log.before["name"] == "A Co" and log.after["timezone"] == "Asia/Dubai"
    # a telecaller cannot open or edit it
    client.logout()
    login(two_tenants["tc_a"])
    assert client.get(reverse("tenants:workspace")).status_code == 403


def test_export_all_is_tenant_scoped_zip_of_csvs(two_tenants, client, login):
    login(two_tenants["owner_a"])
    r = client.post(reverse("tenants:export_all"))
    assert r.status_code == 200 and r["Content-Type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"companies.csv", "contacts.csv", "leads.csv", "activities.csv", "approvals.csv", "users.csv", "audit_log.csv", "README.txt"} <= names
    leads = z.read("leads.csv").decode()
    assert "Lead A" in leads and "Lead B" not in leads
    users = z.read("users.csv").decode()
    assert "owner@a.test" in users and "owner@b.test" not in users
    with rls_bypass():
        assert AuditLog.objects.filter(action="export.all_data", tenant=two_tenants["a"]).exists()


def test_deletion_request_is_owner_only_and_audited(two_tenants, client, login):
    login(two_tenants["mgr_a"])
    assert client.post(reverse("tenants:request_deletion"), {"reason": "closing"}).status_code == 403
    client.logout()
    login(two_tenants["owner_a"])
    assert client.post(reverse("tenants:request_deletion"), {"reason": "closing down"}).status_code == 302
    with rls_bypass():
        assert AuditLog.objects.filter(action="workspace.deletion_requested", note="closing down").exists()
