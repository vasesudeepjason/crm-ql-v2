import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.approvals.models import Approval
from apps.core.tenancy import tenant_context
from apps.crm.models import Company
from apps.field.models import FieldSession, Visit

from .conftest import PW


@pytest.fixture
def field_user(two_tenants):
    from apps.accounts.models import Membership, User

    u = User.objects.create_user("ravi@a.test", PW, name="Ravi Field")
    Membership.objects.create(user=u, tenant=two_tenants["a"], role="field", manager=two_tenants["m_mgr"])
    return u


def test_offline_sync_without_active_day_keeps_capture_time_and_creates_marker_session(two_tenants, field_user, login, client):
    login(field_user)
    captured = (timezone.now() - timedelta(hours=5)).replace(microsecond=0)
    with tenant_context(two_tenants["a"].id):
        company = Company.objects.get(name="A Customer")
    r = client.post(reverse("field:visit_new"), {"company": company.id, "outcome": "other", "notes": "no signal at the depot", "offline_id": "off-1", "captured_at": captured.strftime("%Y-%m-%dT%H:%M:%SZ"), "lat": "17.4", "lng": "78.3", "accuracy": "30"}, HTTP_X_OFFLINE_SYNC="1")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["ok"] and body["title"] == "A Customer"
    v = Visit.all_objects.get(offline_id="off-1")
    assert v.captured_at == captured and v.session.end_source == "auto" and v.session.device == "offline-sync"
    assert Approval.all_objects.filter(kind="visit", object_id=v.id).exists()
    # replay of the same queued item is idempotent
    r = client.post(reverse("field:visit_new"), {"company": company.id, "outcome": "other", "offline_id": "off-1"}, HTTP_X_OFFLINE_SYNC="1")
    assert json.loads(r.content)["duplicate"] is True and Visit.all_objects.filter(offline_id="off-1").count() == 1


def test_offline_sync_attaches_to_the_session_that_was_running(two_tenants, field_user, login, client):
    login(field_user)
    start = timezone.now() - timedelta(hours=6)
    with tenant_context(two_tenants["a"].id):
        s = FieldSession.objects.create(executive=field_user, started_at=start, ended_at=start + timedelta(hours=4), end_source="manual")
    captured = (start + timedelta(hours=2)).replace(microsecond=0)
    r = client.post(reverse("field:visit_new"), {"contact_name": "Walk-in", "outcome": "other", "offline_id": "off-2", "captured_at": captured.strftime("%Y-%m-%dT%H:%M:%SZ")}, HTTP_X_OFFLINE_SYNC="1")
    assert r.status_code == 200
    assert Visit.all_objects.get(offline_id="off-2").session == s


def test_offline_sync_reports_permanent_validation_errors_as_json(two_tenants, field_user, login, client):
    login(field_user)
    r = client.post(reverse("field:visit_new"), {"outcome": "interested", "offline_id": "off-3"}, HTTP_X_OFFLINE_SYNC="1")
    assert r.status_code == 400
    body = json.loads(r.content)
    assert body["permanent"] is True and "__all__" in body["errors"]
    assert not Visit.all_objects.filter(offline_id="off-3").exists()


def test_pwa_endpoints(client, db):
    r = client.get("/sw.js")
    assert r.status_code == 200 and r["Service-Worker-Allowed"] == "/" and "javascript" in r["Content-Type"]
    assert b"sync-visits" in b"".join(r.streaming_content)
    r = client.get(reverse("dashboard:offline"))
    assert r.status_code == 200 and b"offline" in r.content.lower()
    from django.contrib.staticfiles import finders

    assert finders.find("manifest.webmanifest") and finders.find("img/icon-512.png") and finders.find("js/offline-db.js")
