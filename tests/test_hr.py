from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Membership
from apps.core.tenancy import tenant_context
from apps.field.models import FieldSession
from apps.hr import services
from apps.hr.models import Attendance, Employee, Holiday, HRSettings, LeaveRequest, LeaveType
from apps.notifications.models import Notification


@pytest.fixture
def staff(two_tenants):
    """HR user, manager employee (mgr_a), report employee (tc_a) under them, one employee without login; tenant B has one employee."""
    a = two_tenants["a"]
    from apps.accounts.models import User

    hr_user = User.objects.create_user("hr@a.test", "test-password-123", name="Hema Hr")
    Membership.objects.create(user=hr_user, tenant=a, role="hr")
    HRSettings.objects.create(tenant=a, week_off=[6])
    today = timezone.localdate()
    with tenant_context(a.id):
        cl = LeaveType(name="Casual leave", code="CL", days_per_year=Decimal("12"))
        cl.created_by = hr_user
        cl.save()
        mgr = Employee(user=two_tenants["mgr_a"], first_name="Mgr", last_name="A", designation="Manager", join_date=today - timedelta(days=400))
        mgr.created_by = hr_user
        mgr.save()
        tc = Employee(user=two_tenants["tc_a"], first_name="Tc", last_name="A", designation="Telecaller", manager=mgr, join_date=today - timedelta(days=100))
        tc.created_by = hr_user
        tc.save()
        nolog = Employee(first_name="Suresh", last_name="Babu", designation="Assistant", manager=mgr, join_date=today - timedelta(days=30))
        nolog.created_by = hr_user
        nolog.save()
    with tenant_context(two_tenants["b"].id):
        eb = Employee(first_name="Bee", last_name="B", join_date=today)
        eb.created_by = two_tenants["owner_b"]
        eb.save()
    return dict(hr=hr_user, mgr=mgr, tc=tc, nolog=nolog, eb=eb, cl=cl, today=today)


def test_codes_sensitive_fields_and_visibility(two_tenants, staff, login, client):
    with tenant_context(two_tenants["a"].id):
        assert staff["mgr"].code == "EMP-0001" and staff["tc"].code == "EMP-0002"
        e = Employee.objects.get(pk=staff["tc"].pk)
        e.set_sensitive("pan", "ABCDE1234F")
        e.set_sensitive("salary", str(480000_00))
        e.save()
        e.refresh_from_db()
        assert e.pan_enc != "ABCDE1234F" and e.sensitive("pan") == "ABCDE1234F" and e.salary_paise == 480000_00
    # HR sees the restricted card; an admin-less manager viewing their report does not; the Owner does
    client = login(staff["hr"])
    body = client.get(reverse("hr:employee", args=[e.pk])).content.decode()
    assert "ABCDE1234F" in body and "Restricted" in body
    client.logout()
    client = login(two_tenants["mgr_a"])
    body = client.get(reverse("hr:employee", args=[e.pk])).content.decode()
    assert "ABCDE1234F" not in body and "Tc A" in body
    assert client.get(reverse("hr:index")).status_code == 403  # manager has no hr.view
    assert client.get(reverse("hr:employee", args=[staff["mgr"].pk])).status_code == 200  # self
    client.logout()
    client = login(two_tenants["owner_a"])
    assert b"ABCDE1234F" in client.get(reverse("hr:employee", args=[e.pk])).content
    assert client.get(reverse("hr:employee", args=[staff["eb"].pk])).status_code == 404  # other tenant
    client.logout()
    client = login(two_tenants["other_tc"])  # unrelated telecaller
    assert client.get(reverse("hr:employee", args=[e.pk])).status_code == 403


def test_employee_form_create_edit_and_validation(two_tenants, staff, login, client):
    client = login(staff["hr"])
    base = {"first_name": "Lakshmi", "last_name": "Devi", "designation": "Accounts", "employment_type": "full_time", "status": "active", "join_date": staff["today"], "manager": staff["mgr"].pk}
    r = client.post(reverse("hr:employee_new"), {**base, "pan": "bad", "aadhaar": "123"})
    assert r.status_code == 200 and b"PAN looks wrong" in r.content and b"12 digits" in r.content
    r = client.post(reverse("hr:employee_new"), {**base, "pan": "abcde1234f", "aadhaar": "1234 5678 9012", "salary": "600000", "bank_account": "123", "bank_ifsc": "HDFC0001234"})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        e = Employee.objects.get(first_name="Lakshmi")
        assert e.code == "EMP-0004" and e.sensitive("pan") == "ABCDE1234F" and e.sensitive("aadhaar") == "123456789012" and e.salary_paise == 600000_00
    # exited without exit date is refused; with it, the exit checklist appears
    r = client.post(reverse("hr:employee_edit", args=[e.pk]), {**base, "status": "exited"})
    assert b"Set the exit date" in r.content
    r = client.post(reverse("hr:employee_edit", args=[e.pk]), {**base, "status": "exited", "exit_date": staff["today"], "pan": "ABCDE1234F"})
    assert r.status_code == 302
    body = client.get(reverse("hr:employee", args=[e.pk])).content.decode()
    assert "Exit" in body and "Assets returned" in body
    # checklist toggle
    r = client.post(reverse("hr:checklist", args=[e.pk]), {"kind": "exit", "item": "Assets returned"})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        e.refresh_from_db()
        assert e.checklist["exit"]["Assets returned"] is True
    # letters render with placeholders filled
    body = client.get(reverse("hr:letter", args=[e.pk, "offer"])).content.decode()
    assert "Lakshmi Devi" in body and "Accounts" in body and "A Co" in body and "{name}" not in body


def test_documents_upload_serve_delete(two_tenants, staff, login, client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    client = login(staff["hr"])
    e = staff["tc"]
    r = client.post(reverse("hr:doc_upload", args=[e.pk]), {"kind": "id", "title": "Aadhaar", "file": SimpleUploadedFile("a.exe", b"x")})
    assert r.status_code == 422 and b"PDF, JPG" in r.content
    r = client.post(reverse("hr:doc_upload", args=[e.pk]), {"kind": "id", "title": "Aadhaar", "file": SimpleUploadedFile("aadhaar.pdf", b"%PDF-1.4 test"), "expires_on": ""})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        d = e.documents.get()
    assert b"".join(client.get(reverse("hr:doc_file", args=[e.pk, d.pk])).streaming_content).startswith(b"%PDF")
    client.logout()
    client = login(two_tenants["tc_a"])  # the employee sees their own file
    assert client.get(reverse("hr:doc_file", args=[e.pk, d.pk])).status_code == 200
    assert client.get(reverse("hr:doc_upload", args=[e.pk])).status_code == 403
    client.logout()
    client = login(two_tenants["other_tc"])
    assert client.get(reverse("hr:doc_file", args=[e.pk, d.pk])).status_code == 403
    client.logout()
    client = login(staff["hr"])
    assert client.post(reverse("hr:doc_delete", args=[e.pk, d.pk])).status_code == 302
    with tenant_context(two_tenants["a"].id):
        assert not e.documents.exists()


def test_check_in_out_and_field_session_posts_attendance(two_tenants, staff, login, client):
    client = login(two_tenants["tc_a"])
    r = client.get(reverse("hr:me"))
    assert r.status_code == 200 and b"Check in" in r.content
    assert client.post(reverse("hr:punch"), {"action": "in"}).status_code == 302
    with tenant_context(two_tenants["a"].id):
        a = Attendance.objects.get(employee=staff["tc"], date=staff["today"])
        assert a.check_in and a.status == "present" and a.source == "self"
    assert b"Check out" in client.get(reverse("hr:me")).content
    assert client.post(reverse("hr:punch"), {"action": "out"}).status_code == 302
    with tenant_context(two_tenants["a"].id):
        a.refresh_from_db()
        assert a.check_out and a.status == "half_day"  # seconds of work → below the half-day threshold
    # a field session for the manager's user posts attendance automatically
    with tenant_context(two_tenants["a"].id):
        s = FieldSession(executive=two_tenants["mgr_a"])
        s.created_by = two_tenants["mgr_a"]
        s.save()
        a2 = Attendance.objects.get(employee=staff["mgr"], date=staff["today"])
        assert a2.source == "field" and a2.check_in == s.started_at and a2.check_out is None
        s.end("manual")
        a2.refresh_from_db()
        assert a2.check_out == s.ended_at
    # someone without an employee record gets the explainer, not an error
    client.logout()
    client = login(two_tenants["other_tc"])
    assert b"No employee record" in client.get(reverse("hr:me")).content
    assert client.post(reverse("hr:punch"), {"action": "in"}).status_code == 403


def test_leave_apply_balance_approve_reject_cancel(two_tenants, staff, login, client):
    cfg = HRSettings.objects.get(tenant=two_tenants["a"])
    today = staff["today"]
    # pick a Monday next week so week-offs are predictable
    start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    with tenant_context(two_tenants["a"].id):
        h = Holiday(date=start + timedelta(days=2), name="Festival")
        h.created_by = staff["hr"]
        h.save()
        assert services.working_days(cfg, start, start + timedelta(days=6)) == Decimal("5")  # Mon–Sun minus holiday Wed minus Sunday
    client = login(two_tenants["tc_a"])
    mail.outbox.clear()
    r = client.post(reverse("hr:leave_apply"), {"leave_type": staff["cl"].pk, "start_date": start, "end_date": start + timedelta(days=6), "reason": "Wedding"})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        lr = LeaveRequest.objects.get(employee=staff["tc"])
        assert lr.days == Decimal("5") and lr.status == "pending"
        assert Notification.objects.filter(user=two_tenants["mgr_a"], kind="leave").exists()  # manager notified
        bal = next(b for b in services.balances(staff["tc"]) if b["type"].pk == staff["cl"].pk)
        assert bal["pending"] == Decimal("5") and bal["available"] == Decimal("7")
    assert mail.outbox and mail.outbox[0].to == [two_tenants["mgr_a"].email]
    # overlapping request refused; over-balance refused
    assert b"already have leave" in client.post(reverse("hr:leave_apply"), {"leave_type": staff["cl"].pk, "start_date": start + timedelta(days=1), "end_date": start + timedelta(days=1)}).content
    assert b"left; this needs" in client.post(reverse("hr:leave_apply"), {"leave_type": staff["cl"].pk, "start_date": start + timedelta(days=14), "end_date": start + timedelta(days=25)}).content
    # the manager approves from their inbox; attendance rows appear; employee is notified
    client.logout()
    client = login(two_tenants["mgr_a"])
    body = client.get(reverse("hr:leave")).content.decode()
    assert "Wedding" in body and "Review" in body
    assert client.post(reverse("hr:leave_decide", args=[lr.pk]), {"decision": "reject", "note": ""}).status_code == 422  # reason needed
    assert client.post(reverse("hr:leave_decide", args=[lr.pk]), {"decision": "approve", "note": "Enjoy"}).status_code == 204
    with tenant_context(two_tenants["a"].id):
        lr.refresh_from_db()
        assert lr.status == "approved" and lr.decided_by == two_tenants["mgr_a"]
        assert Attendance.objects.filter(employee=staff["tc"], status="leave", source="leave").count() == 5
        assert Notification.objects.filter(user=two_tenants["tc_a"], kind="leave", title__contains="approved").exists()
    # cancelling an approved future leave removes the attendance rows and restores the balance
    client.logout()
    client = login(two_tenants["tc_a"])
    assert client.post(reverse("hr:leave_cancel", args=[lr.pk])).status_code == 302
    with tenant_context(two_tenants["a"].id):
        lr.refresh_from_db()
        assert lr.status == "cancelled" and not Attendance.objects.filter(employee=staff["tc"], source="leave").exists()
        assert next(b for b in services.balances(staff["tc"]) if b["type"].pk == staff["cl"].pk)["available"] == Decimal("12")
    # an unrelated user cannot decide
    client.logout()
    client = login(two_tenants["other_tc"])
    assert client.get(reverse("hr:leave")).status_code == 403
    # HR records leave on behalf of someone without a login: approved at once
    client.logout()
    client = login(staff["hr"])
    assert client.post(reverse("hr:leave_for", args=[staff["nolog"].pk]), {"leave_type": staff["cl"].pk, "start_date": start, "end_date": start, "reason": ""}).status_code == 204
    with tenant_context(two_tenants["a"].id):
        assert LeaveRequest.objects.get(employee=staff["nolog"]).status == "approved"


def test_attendance_register_marking_and_export(two_tenants, staff, login, client):
    today = staff["today"]
    client = login(staff["hr"])
    r = client.get(reverse("hr:attendance"))
    assert r.status_code == 200 and b"Register" in r.content and b"Suresh Babu" in r.content
    r = client.post(reverse("hr:attendance_mark"), {"employee": staff["nolog"].pk, "date": today.isoformat(), "status": "absent", "note": "No call"})
    assert r.status_code == 302
    with tenant_context(two_tenants["a"].id):
        a = Attendance.objects.get(employee=staff["nolog"], date=today)
        assert a.status == "absent" and a.source == "hr"
        reg = services.month_register([staff["nolog"]], today.year, today.month, HRSettings.objects.get(tenant=two_tenants["a"]))
        s = reg["rows"][0]["summary"]
        assert s["absent"] == 1 and reg["rows"][0]["cells"][today.day - 1] == ("A", "absent")
    r = client.get(reverse("hr:attendance_export"), {"y": today.year, "m": today.month})
    assert r.status_code == 200 and "Suresh Babu" in r.content.decode("utf-8-sig") and "Payable days" in r.content.decode("utf-8-sig")
    assert client.post(reverse("hr:attendance_mark"), {"employee": staff["nolog"].pk, "date": today.isoformat(), "status": "clear"}).status_code == 302
    with tenant_context(two_tenants["a"].id):
        assert not Attendance.objects.filter(employee=staff["nolog"], date=today).exists()
    # a telecaller cannot see the register
    client.logout()
    client = login(two_tenants["tc_a"])
    assert client.get(reverse("hr:attendance")).status_code == 403


def test_settings_types_holidays_departments(two_tenants, staff, login, client):
    client = login(staff["hr"])
    assert client.get(reverse("hr:settings")).status_code == 200
    r = client.post(reverse("hr:settings"), {"work_start": "09:00", "work_end": "18:00", "half_day_hours": "4.5", "week_off_days": ["5", "6"], "onboarding_items": "A\nB", "exit_items": "C", "offer_letter": "Hi {name}", "appointment_letter": "Hello {name} at {company}"})
    assert r.status_code == 302
    cfg = HRSettings.objects.get(tenant=two_tenants["a"])
    assert cfg.week_off == [5, 6] and cfg.onboarding_list == ["A", "B"] and cfg.is_week_off(date(2026, 10, 31))  # a Saturday
    assert client.post(reverse("hr:leave_type_new"), {"name": "Sick leave", "code": "SL", "days_per_year": "8", "paid": "on", "color": "yellow", "is_active": "on"}).status_code == 204
    assert client.post(reverse("hr:holiday_new"), {"date": "2026-12-25", "name": "Christmas"}).status_code == 204
    assert client.post(reverse("hr:department_new"), {"name": "Accounts"}).status_code == 204
    with tenant_context(two_tenants["a"].id):
        assert LeaveType.objects.filter(code="SL").exists() and Holiday.objects.filter(name="Christmas").exists()
        h = Holiday.objects.get(name="Christmas")
    assert client.post(reverse("hr:holiday_edit", args=[h.pk]), {"delete": "1"}).status_code == 204
    with tenant_context(two_tenants["a"].id):
        assert not Holiday.objects.filter(name="Christmas").exists()
    client.logout()
    client = login(two_tenants["mgr_a"])
    assert client.get(reverse("hr:settings")).status_code == 403
