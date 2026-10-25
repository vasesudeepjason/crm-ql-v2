import io
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.approvals.models import Approval
from apps.core.models import AuditLog
from apps.core.tenancy import rls_bypass, tenant_context
from apps.crm.models import Activity, Company, Lead
from apps.field.models import FieldSession, Visit
from apps.field.services import close_stale_sessions

from .conftest import PW


@pytest.fixture
def field_user(two_tenants):
    from apps.accounts.models import Membership, User

    u = User.objects.create_user("ravi@a.test", PW, name="Ravi Field")
    Membership.objects.create(user=u, tenant=two_tenants["a"], role="field", manager=two_tenants["m_mgr"])
    return u


def _jpeg(w=2400, h=1600):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 40)).save(buf, "JPEG")
    return SimpleUploadedFile("photo.jpg", buf.getvalue(), content_type="image/jpeg")


def test_visit_needs_an_active_day(two_tenants, field_user, login, client):
    login(field_user)
    r = client.get(reverse("field:my_day"))
    assert r.status_code == 200 and b"Start day" in r.content
    assert client.get(reverse("field:visit_new")).status_code == 302  # bounced: no session


def test_full_day_photo_location_approval_and_followup(two_tenants, field_user, login, client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    login(field_user)
    r = client.post(reverse("field:session_start"), {"lat": "17.4401", "lng": "78.3489", "accuracy": "12"})
    assert r.status_code == 302
    s = FieldSession.all_objects.get(executive=field_user)
    assert s.is_active and s.start_lat == 17.4401
    with tenant_context(two_tenants["a"].id):
        company = Company.objects.get(name="A Customer")
        lead = Lead.objects.create(name="Visit lead", company=company, owner=field_user, stage="contacted")

    r = client.post(reverse("field:visit_new"), {
        "company": company.id, "lead": lead.id, "contact_name": "Purchase head", "outcome": "interested", "notes": "Wants AMC renewed",
        "next_follow_up": "2026-11-10", "duration_minutes": "35", "lat": "17.4402", "lng": "78.3490", "accuracy": "9", "offline_id": "abc-1", "photo": _jpeg(),
    })
    assert r.status_code == 302
    v = Visit.all_objects.get(executive=field_user)
    assert v.status == "pending" and v.session == s and v.lat == 17.4402 and not v.low_accuracy
    assert v.photo and Image.open(v.photo.path).size[0] <= 1600  # server-side shrink
    ap = Approval.all_objects.get(kind="visit", object_id=v.id)
    assert ap.submitter == field_user and ap.summary["outcome"] == "Interested" and ap.summary["has_photo"] is True

    # offline retry with the same id does not duplicate
    client.post(reverse("field:visit_new"), {"company": company.id, "outcome": "other", "lat": "1", "lng": "1", "offline_id": "abc-1"})
    assert Visit.all_objects.filter(executive=field_user).count() == 1

    # the executive sees their photo; another tenant's owner does not
    assert client.get(reverse("field:visit_photo", args=[v.id])).status_code == 200
    client.logout()
    login(two_tenants["owner_b"])
    assert client.get(reverse("field:visit_photo", args=[v.id])).status_code == 404

    # the manager approves → activity on the lead, follow-up moved, stage advanced
    client.logout()
    login(two_tenants["mgr_a"])
    assert client.get(reverse("field:visit_photo", args=[v.id])).status_code == 200
    r = client.get(reverse("approvals:detail", args=[ap.id]))
    assert b"visit_photo" in r.content or b"/photo/" in r.content
    r = client.post(reverse("approvals:approve", args=[ap.id]))
    assert r.status_code == 200
    with tenant_context(two_tenants["a"].id):
        v.refresh_from_db()
        lead.refresh_from_db()
        assert v.status == "approved"
        assert lead.next_follow_up == date(2026, 11, 10) and lead.stage == "interested"
        assert Activity.objects.filter(lead=lead, type="meeting").exists()

    # end the day
    client.logout()
    login(field_user)
    client.post(reverse("field:session_end"), {"lat": "17.5", "lng": "78.5"})
    s.refresh_from_db()
    assert not s.is_active and s.end_source == "manual" and s.end_lat == 17.5


def test_rejection_marks_visit_and_does_not_touch_lead(two_tenants, field_user, login, client):
    login(field_user)
    client.post(reverse("field:session_start"), {})
    with tenant_context(two_tenants["a"].id):
        lead = Lead.objects.create(name="L", company=Company.objects.get(name="A Customer"), owner=field_user, stage="new")
    client.post(reverse("field:visit_new"), {"lead": lead.id, "outcome": "order", "notes": "x"})
    v = Visit.all_objects.get()
    ap = Approval.all_objects.get(object_id=v.id, kind="visit")
    client.logout()
    login(two_tenants["mgr_a"])
    client.post(reverse("approvals:reject", args=[ap.id]), {"reason": "Photo missing"})
    with tenant_context(two_tenants["a"].id):
        v.refresh_from_db()
        lead.refresh_from_db()
        assert v.status == "rejected" and lead.stage == "new" and not Activity.objects.filter(lead=lead).exists()


def test_stale_sessions_auto_close(two_tenants, field_user):
    with tenant_context(two_tenants["a"].id):
        old = FieldSession.all_objects.create(executive=field_user, started_at=timezone.now() - timedelta(days=1, hours=3))
        fresh = FieldSession.all_objects.create(executive=field_user, started_at=timezone.now() - timedelta(minutes=5))
        n = close_stale_sessions("Asia/Kolkata")
        old.refresh_from_db()
        fresh.refresh_from_db()
        assert n == 1 and old.end_source == "auto" and fresh.is_active


def test_low_accuracy_and_mock_are_flagged_for_the_manager(two_tenants, field_user, login, client):
    login(field_user)
    client.post(reverse("field:session_start"), {})
    client.post(reverse("field:visit_new"), {"contact_name": "Walk-in", "outcome": "other", "lat": "17.4", "lng": "78.3", "accuracy": "900", "mock_location": "on"})
    v = Visit.all_objects.get()
    assert v.low_accuracy and v.mock_location
    ap = Approval.all_objects.get(object_id=v.id, kind="visit")
    assert ap.summary["low_accuracy"] is True and ap.summary["mock"] is True
    client.logout()
    login(two_tenants["mgr_a"])
    r = client.get(reverse("approvals:queue"), {"kind": "visit"})
    assert b"low accuracy" in r.content and b"mock location?" in r.content


def test_queue_filters_and_bulk_approve(two_tenants, field_user, login, client):
    login(field_user)
    client.post(reverse("field:session_start"), {})
    for i in range(3):
        client.post(reverse("field:visit_new"), {"contact_name": f"Shop {i}", "outcome": "other"})
    client.logout()
    login(two_tenants["mgr_a"])
    r = client.get(reverse("approvals:queue"), {"kind": "visit", "who": field_user.id})
    assert r.status_code == 200 and r.content.count(b"Shop ") == 3
    r = client.get(reverse("approvals:queue"), {"kind": "lead"})
    assert b"Shop " not in r.content
    import zoneinfo

    ist_tomorrow = timezone.now().astimezone(zoneinfo.ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)  # requests run in the tenant zone
    r = client.get(reverse("approvals:queue"), {"from": ist_tomorrow.isoformat()})
    assert b"Shop " not in r.content
    ids = list(Approval.all_objects.filter(kind="visit").values_list("id", flat=True))
    r = client.post(reverse("approvals:bulk_approve"), {"ids": ids + [two_tenants["appr_other"].id]})  # last one is outside the team
    assert r.status_code == 302
    assert Approval.all_objects.filter(kind="visit", status="approved").count() == 3
    assert Approval.all_objects.get(pk=two_tenants["appr_other"].id).status == "pending"
    with rls_bypass():
        assert AuditLog.objects.filter(action="approval.approve", after__bulk=True).count() == 3


def test_team_page_scopes_to_manager_team_and_can_end_a_day(two_tenants, field_user, login, client):
    login(field_user)
    client.post(reverse("field:session_start"), {"lat": "17.4", "lng": "78.3"})
    client.logout()
    login(two_tenants["mgr_a"])
    r = client.get(reverse("field:team"))
    assert r.status_code == 200 and b"Ravi Field" in r.content and b"On the road" in r.content
    s = FieldSession.all_objects.get(executive=field_user)
    client.post(reverse("field:end_session_for", args=[s.id]))
    s.refresh_from_db()
    assert s.end_source == "manager"
    client.logout()
    login(two_tenants["tc_a"])
    assert client.get(reverse("field:team")).status_code == 403
    client.logout()
    login(two_tenants["owner_b"])
    assert b"Ravi Field" not in client.get(reverse("field:team")).content
