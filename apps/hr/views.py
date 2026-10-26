import csv
import io
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import audit
from apps.core.permissions import has_perm, require_perm

from . import services
from .forms import DepartmentForm, DocumentForm, EmployeeForm, HolidayForm, HRSettingsForm, LeaveForm, LeaveTypeForm
from .models import Attendance, Department, Employee, EmployeeDocument, Holiday, HRSettings, LeaveAdjustment, LeaveRequest, LeaveType


def _refresh():
    r = HttpResponse(status=204)
    r["HX-Refresh"] = "true"
    return r


def _err(msg):
    return HttpResponse(f'<div class="field-err" role="alert">{msg}</div>', status=422)


def _emp(request, pk) -> Employee:
    emp = get_object_or_404(Employee.objects.select_related("department", "manager", "user"), pk=pk)
    if not services.can_view_employee(request, emp):
        raise PermissionDenied("You cannot view this employee.")
    return emp


# ---------- directory ----------

@login_required
@require_perm("hr.view")
def index(request):
    q = (request.GET.get("q") or "").strip()
    dept = request.GET.get("dept", "")
    status = request.GET.get("status", "active")
    qs = Employee.objects.select_related("department", "manager", "user")
    if status == "active":
        qs = qs.exclude(status="exited")
    elif status != "all":
        qs = qs.filter(status=status)
    if dept.isdigit():
        qs = qs.filter(department_id=int(dept))
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(code__icontains=q) | Q(designation__icontains=q) | Q(email__icontains=q))
    today = timezone.localdate()
    present = set(Attendance.objects.filter(date=today).exclude(status__in=["absent", "leave"]).values_list("employee_id", flat=True))
    on_leave = set(Attendance.objects.filter(date=today, status="leave").values_list("employee_id", flat=True))
    rows = [{"e": e, "today": "present" if e.pk in present else ("leave" if e.pk in on_leave else "")} for e in qs]
    depts = Department.objects.annotate(n=Count("employees", filter=Q(employees__status__in=["active", "notice"])))
    counts = {"active": Employee.objects.exclude(status="exited").count(), "exited": Employee.objects.filter(status="exited").count(), "present": len(present), "leave": len(on_leave)}
    return render(request, "hr/index.html", {"rows": rows, "q": q, "dept": dept, "status": status, "depts": depts, "counts": counts, "can_edit": has_perm(request, "hr.edit"), "today": today})


@login_required
def employee_form(request, pk=None):
    if not has_perm(request, "hr.edit"):
        raise PermissionDenied("HR maintains employee records.")
    emp = get_object_or_404(Employee, pk=pk) if pk else None
    show_sens = services.can_see_sensitive(request)
    form = EmployeeForm(request.POST or None, request.FILES or None, instance=emp, tenant=request.tenant, show_sensitive=show_sens)
    if request.method == "POST" and form.is_valid():
        before = {"status": emp.status, "designation": emp.designation, "manager": emp.manager_id, "department": emp.department_id} if emp else None
        obj = form.save(commit=False)
        if emp is None:
            obj.created_by = request.user
        obj.save()
        audit(request, "hr.employee_edit" if emp else "hr.employee_create", obj, before=before, after={"status": obj.status, "designation": obj.designation, "manager": obj.manager_id, "department": obj.department_id, "sensitive_updated": show_sens})
        messages.success(request, f"{obj.name} {'updated' if emp else 'added'} ({obj.code}).")
        return redirect("hr:employee", pk=obj.pk)
    return render(request, "hr/employee_form.html", {"form": form, "emp": emp, "show_sensitive": show_sens})


@login_required
def employee_detail(request, pk):
    emp = _emp(request, pk)
    cfg = HRSettings.for_tenant(request.tenant)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    att = list(Attendance.objects.filter(employee=emp, date__gte=month_start, date__lte=today).order_by("-date"))
    reg = services.month_register([emp], today.year, today.month, cfg)["rows"][0]["summary"]
    ctx = {"emp": emp, "cfg": cfg, "docs": emp.documents.all(), "att": att[:10], "att_summary": reg, "balances": services.balances(emp), "leaves": emp.leave_requests.select_related("leave_type", "decided_by")[:10],
           "onboarding": emp.checklist_state("onboarding", cfg.onboarding_list), "exit": emp.checklist_state("exit", cfg.exit_list), "reports": emp.reports.exclude(status="exited"),
           "can_edit": has_perm(request, "hr.edit"), "show_sensitive": services.can_see_sensitive(request), "is_me": bool(emp.user_id == request.user.id), "today": today,
           "sens": {f: emp.sensitive(f) for f in Employee.SENSITIVE} if services.can_see_sensitive(request) else {}}
    return render(request, "hr/employee_detail.html", ctx)


@login_required
@require_perm("hr.edit")
@require_POST
def checklist_toggle(request, pk):
    emp = _emp(request, pk)
    kind, item = request.POST.get("kind"), request.POST.get("item", "")
    if kind not in ("onboarding", "exit") or not item:
        return HttpResponse(status=400)
    state = emp.checklist.get(kind, {})
    state[item] = not state.get(item)
    emp.checklist[kind] = state
    emp.save(update_fields=["checklist", "updated_at"])
    audit(request, "hr.checklist", emp, after={"kind": kind, "item": item, "done": state[item]})
    return _refresh()


# ---------- documents ----------

@login_required
def doc_upload(request, pk):
    emp = _emp(request, pk)
    if not has_perm(request, "hr.edit"):
        raise PermissionDenied("HR uploads documents.")
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not form.is_valid():
            return _err("; ".join(e[0] for e in form.errors.values()))
        d = form.save(commit=False)
        d.employee, d.created_by = emp, request.user
        d.save()
        audit(request, "hr.document_upload", d, after={"employee": emp.code, "kind": d.kind, "title": d.title})
        messages.success(request, f"{d.title} uploaded.")
        return _refresh()
    return render(request, "hr/_doc_drawer.html", {"emp": emp, "form": form})


@login_required
def doc_file(request, pk, doc_id):
    emp = _emp(request, pk)
    d = get_object_or_404(EmployeeDocument, pk=doc_id, employee=emp)
    if not (has_perm(request, "hr.view") or emp.user_id == request.user.id):
        raise PermissionDenied
    name = d.file.name.lower()
    ctype = "application/pdf" if name.endswith(".pdf") else "image/jpeg" if name.endswith((".jpg", ".jpeg")) else "image/png" if name.endswith(".png") else "application/octet-stream"
    resp = FileResponse(d.file.open("rb"), content_type=ctype, as_attachment=request.GET.get("dl") == "1", filename=d.file.name.rsplit("/", 1)[-1])
    resp["Cache-Control"] = "private, max-age=300"
    return resp


@login_required
@require_perm("hr.edit")
@require_POST
def doc_delete(request, pk, doc_id):
    emp = _emp(request, pk)
    d = get_object_or_404(EmployeeDocument, pk=doc_id, employee=emp)
    audit(request, "hr.document_delete", d, before={"title": d.title, "kind": d.kind})
    d.file.delete(save=False)
    d.delete()
    messages.success(request, "Document removed.")
    return redirect("hr:employee", pk=emp.pk)


# ---------- self-service ----------

@login_required
def me(request):
    emp = services.my_employee(request)
    if emp is None:
        return render(request, "hr/me_none.html", {"is_hr": has_perm(request, "hr.edit")})
    cfg = HRSettings.for_tenant(request.tenant)
    today = timezone.localdate()
    reg = services.month_register([emp], today.year, today.month, cfg)
    form = LeaveForm(initial={"start_date": today + timedelta(days=1), "end_date": today + timedelta(days=1)})
    ctx = {"emp": emp, "today_att": services.today_for(emp), "reg": reg, "row": reg["rows"][0], "balances": services.balances(emp), "leaves": emp.leave_requests.select_related("leave_type", "decided_by")[:12], "form": form,
           "holidays": Holiday.objects.filter(date__gte=today)[:6], "docs": emp.documents.all(), "cfg": cfg, "today": today, "now": timezone.localtime(), "pending_for_me": LeaveRequest.objects.filter(status="pending", employee__manager=emp).count()}
    return render(request, "hr/me.html", ctx)


@login_required
@require_POST
def punch(request):
    emp = services.my_employee(request)
    if emp is None:
        raise PermissionDenied("No employee record is linked to your login.")
    if request.POST.get("action") == "out":
        att = services.check_out(emp)
        audit(request, "hr.check_out", emp, after={"at": timezone.localtime().strftime("%H:%M")})
        messages.success(request, f"Checked out at {timezone.localtime():%H:%M}." + (f" {att.hours} hours today." if att and att.hours is not None else ""))
    else:
        services.check_in(emp)
        audit(request, "hr.check_in", emp, after={"at": timezone.localtime().strftime("%H:%M")})
        messages.success(request, f"Checked in at {timezone.localtime():%H:%M}.")
    return redirect(request.POST.get("next") or "hr:me")


@login_required
def leave_apply(request, pk=None):
    """Apply for yourself (pk None) or, as HR, on behalf of an employee."""
    emp = services.my_employee(request) if pk is None else _emp(request, pk)
    if emp is None:
        raise PermissionDenied("No employee record is linked to your login.")
    if pk is not None and not has_perm(request, "hr.edit") and emp.user_id != request.user.id:
        raise PermissionDenied("Only HR applies on someone's behalf.")
    cfg = HRSettings.for_tenant(request.tenant)
    form = LeaveForm(request.POST or None, initial={"start_date": timezone.localdate() + timedelta(days=1), "end_date": timezone.localdate() + timedelta(days=1)})
    if request.method == "POST":
        if not form.is_valid():
            return _err("; ".join(e[0] for e in form.errors.values()))
        d = form.cleaned_data
        try:
            lr = services.apply_leave(emp, d["leave_type"], d["start_date"], d["end_date"], d["half_day"], d.get("reason", ""), request.user, cfg)
        except ValueError as e:
            return _err(str(e))
        audit(request, "leave.apply", lr, after={"employee": emp.code, "type": lr.leave_type.code, "days": str(lr.days), "from": str(lr.start_date), "to": str(lr.end_date)})
        if has_perm(request, "hr.edit") and pk is not None:  # HR applying on behalf: approve at once
            services.decide_leave(lr, request.user, True, "Recorded by HR", cfg)
            audit(request, "leave.approve", lr, after={"by": "hr"})
        messages.success(request, f"{lr.days:g} day{'s' if lr.days != 1 else ''} {lr.leave_type.name} {'recorded' if pk else 'requested'}.")
        return _refresh()
    return render(request, "hr/_leave_drawer.html", {"emp": emp, "form": form, "balances": services.balances(emp), "on_behalf": pk is not None})


@login_required
@require_POST
def leave_cancel(request, pk):
    lr = get_object_or_404(LeaveRequest.objects.select_related("employee"), pk=pk)
    if not (lr.employee.user_id == request.user.id or has_perm(request, "hr.edit")):
        raise PermissionDenied
    if lr.status not in ("pending", "approved") or (lr.status == "approved" and lr.start_date < timezone.localdate() and not has_perm(request, "hr.edit")):
        messages.error(request, "That leave can no longer be cancelled.")
        return redirect("hr:me")
    services.cancel_leave(lr, request.user)
    audit(request, "leave.cancel", lr)
    messages.success(request, "Leave cancelled.")
    return redirect(request.POST.get("next") or "hr:me")


# ---------- leave (approvers / HR) ----------

@login_required
def leave(request):
    me = services.my_employee(request)
    hr = has_perm(request, "hr.view")
    if not hr and me is None:
        raise PermissionDenied
    status = request.GET.get("status", "pending")
    qs = LeaveRequest.objects.select_related("employee", "employee__department", "leave_type", "decided_by")
    if not hr:
        qs = qs.filter(employee__manager=me)
    if status != "all":
        qs = qs.filter(status=status)
    counts = dict((qs.model.objects.filter(employee__manager=me) if not hr else qs.model.objects.all()).values_list("status").annotate(n=Count("id")))
    today = timezone.localdate()
    upcoming = LeaveRequest.objects.filter(status="approved", end_date__gte=today, start_date__lte=today + timedelta(days=14)).select_related("employee", "leave_type").order_by("start_date") if hr else []
    return render(request, "hr/leave.html", {"rows": qs[:200], "status": status, "counts": counts, "upcoming": upcoming, "today": today, "is_hr": hr, "types": LeaveType.objects.all(), "holidays": Holiday.objects.filter(date__gte=today)[:8], "can_edit": has_perm(request, "hr.edit")})


@login_required
def leave_decide(request, pk):
    lr = get_object_or_404(LeaveRequest.objects.select_related("employee", "leave_type"), pk=pk)
    if not services.is_approver_for(request, lr.employee):
        raise PermissionDenied("You do not approve leave for this employee.")
    if not lr.is_open:
        raise PermissionDenied("Already decided.")
    if request.method == "POST":
        approve = request.POST.get("decision") == "approve"
        note = request.POST.get("note", "")
        if not approve and not note.strip():
            return _err("Give a reason when rejecting.")
        services.decide_leave(lr, request.user, approve, note, HRSettings.for_tenant(request.tenant))
        audit(request, "leave.approve" if approve else "leave.reject", lr, after={"employee": lr.employee.code, "note": note})
        messages.success(request, f"{lr.employee.first_name}'s {lr.leave_type.name} {'approved' if approve else 'rejected'}.")
        return _refresh()
    return render(request, "hr/_leave_decide_drawer.html", {"lr": lr, "balances": [b for b in services.balances(lr.employee, lr.fy) if b["type"].pk == lr.leave_type_id], "overlap": LeaveRequest.objects.filter(status="approved", start_date__lte=lr.end_date, end_date__gte=lr.start_date, employee__department=lr.employee.department).exclude(pk=lr.pk).select_related("employee")[:5]})


# ---------- attendance (HR) ----------

@login_required
@require_perm("attendance.view")
def attendance(request):
    today = timezone.localdate()
    try:
        y, m = int(request.GET.get("y", today.year)), int(request.GET.get("m", today.month))
        date(y, m, 1)
    except ValueError:
        y, m = today.year, today.month
    cfg = HRSettings.for_tenant(request.tenant)
    dept = request.GET.get("dept", "")
    emps = Employee.objects.select_related("department").exclude(status="exited")
    if dept.isdigit():
        emps = emps.filter(department_id=int(dept))
    reg = services.month_register(list(emps), y, m, cfg)
    first = date(y, m, 1)
    prev = (first - timedelta(days=1)).replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    today_rows = []
    if (y, m) == (today.year, today.month):
        att = {a.employee_id: a for a in Attendance.objects.filter(date=today)}
        today_rows = [(e, att.get(e.pk)) for e in emps]
    return render(request, "hr/attendance.html", {"reg": reg, "prev": prev, "next": nxt, "dept": dept, "depts": Department.objects.all(), "today": today, "today_rows": today_rows, "can_edit": has_perm(request, "attendance.edit"), "is_today": (y, m) == (today.year, today.month), "cfg": cfg})


@login_required
@require_perm("attendance.edit")
@require_POST
def attendance_mark(request):
    """HR marks a day: employee, date, status (present/half_day/absent/wfh) or clears it."""
    emp = get_object_or_404(Employee, pk=request.POST.get("employee"))
    d = date.fromisoformat(request.POST.get("date"))
    status = request.POST.get("status", "")
    if status == "clear":
        Attendance.objects.filter(employee=emp, date=d).delete()
    elif status in Attendance.Status.values:
        Attendance.objects.update_or_create(employee=emp, date=d, defaults={"status": status, "source": Attendance.Source.HR, "note": request.POST.get("note", "")[:160], "tenant_id": emp.tenant_id})
    else:
        return HttpResponse(status=400)
    audit(request, "attendance.mark", emp, after={"date": str(d), "status": status})
    return redirect(request.POST.get("next") or f"{request.path.rsplit('/mark', 1)[0]}/?y={d.year}&m={d.month}")


@login_required
@require_perm("attendance.view")
def attendance_export(request):
    today = timezone.localdate()
    y, m = int(request.GET.get("y", today.year)), int(request.GET.get("m", today.month))
    cfg = HRSettings.for_tenant(request.tenant)
    reg = services.month_register(list(Employee.objects.select_related("department").exclude(status="exited")), y, m, cfg)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Code", "Employee", "Department", "Designation", "Present", "Half days", "WFH", "Leave", "Absent", "Holidays", "Week-offs", "Unmarked", "Payable days", *[d.strftime("%d") for d in reg["days"]]])
    for r in reg["rows"]:
        e, s = r["employee"], r["summary"]
        w.writerow([e.code, e.name, e.department.name if e.department else "", e.designation, s["present"], s["half"], s["wfh"], s["leave"], s["absent"], s["holiday"], s["weekoff"], s["unmarked"], s["payable"], *[c[0] for c in r["cells"]]])
    resp = HttpResponse("﻿" + out.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="attendance-{y}-{m:02d}.csv"'
    audit(request, "export.attendance", note=f"{y}-{m:02d}")
    return resp


# ---------- settings: leave types, holidays, departments, checklists, letters ----------

@login_required
@require_perm("hr.edit")
def hr_settings(request):
    cfg = HRSettings.for_tenant(request.tenant)
    form = HRSettingsForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "hr.settings", request.tenant, after={"week_off": cfg.week_off, "work": f"{cfg.work_start}-{cfg.work_end}"})
        messages.success(request, "HR settings saved.")
        return redirect("hr:settings")
    return render(request, "hr/settings.html", {"form": form, "cfg": cfg, "types": LeaveType.objects.all(), "holidays": Holiday.objects.filter(date__gte=timezone.localdate() - timedelta(days=90)), "depts": Department.objects.select_related("head").annotate(n=Count("employees")), "placeholders": ["name", "designation", "department", "join_date", "salary", "company", "manager", "code", "today"]})


@login_required
@require_perm("hr.edit")
def leave_type_form(request, pk=None):
    obj = get_object_or_404(LeaveType, pk=pk) if pk else None
    form = LeaveTypeForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if not form.is_valid():
            return _err("; ".join(e[0] for e in form.errors.values()))
        o = form.save(commit=False)
        if obj is None:
            o.created_by = request.user
        o.save()
        audit(request, "leave.type", o, after={"name": o.name, "days": str(o.days_per_year)})
        return _refresh()
    return render(request, "hr/_small_drawer.html", {"title": "Leave type", "form": form, "action": request.path})


@login_required
@require_perm("hr.edit")
def holiday_form(request, pk=None):
    obj = get_object_or_404(Holiday, pk=pk) if pk else None
    if request.method == "POST" and request.POST.get("delete") and obj:
        obj.delete()
        return _refresh()
    form = HolidayForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if not form.is_valid():
            return _err("; ".join(e[0] for e in form.errors.values()))
        o = form.save(commit=False)
        if obj is None:
            o.created_by = request.user
        o.save()
        audit(request, "hr.holiday", o, after={"date": str(o.date), "name": o.name})
        return _refresh()
    return render(request, "hr/_small_drawer.html", {"title": "Holiday", "form": form, "action": request.path, "deletable": obj is not None})


@login_required
@require_perm("hr.edit")
def department_form(request, pk=None):
    obj = get_object_or_404(Department, pk=pk) if pk else None
    form = DepartmentForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if not form.is_valid():
            return _err("; ".join(e[0] for e in form.errors.values()))
        o = form.save(commit=False)
        if obj is None:
            o.created_by = request.user
        o.save()
        audit(request, "hr.department", o, after={"name": o.name})
        return _refresh()
    return render(request, "hr/_small_drawer.html", {"title": "Department", "form": form, "action": request.path})


@login_required
@require_perm("hr.edit")
@require_POST
def leave_adjust(request, pk):
    emp = _emp(request, pk)
    lt = get_object_or_404(LeaveType, pk=request.POST.get("leave_type"))
    try:
        days = float(request.POST.get("days", "0"))
    except ValueError:
        days = 0
    if not days:
        messages.error(request, "Enter the days to credit (positive) or debit (negative).")
        return redirect("hr:employee", pk=emp.pk)
    a = LeaveAdjustment(employee=emp, leave_type=lt, year=services.fy_of(timezone.localdate()), days=days, note=request.POST.get("note", "")[:160])
    a.created_by = request.user
    a.save()
    audit(request, "leave.adjust", a, after={"employee": emp.code, "type": lt.code, "days": days})
    messages.success(request, f"{days:+g} {lt.name} days for {emp.first_name}.")
    return redirect("hr:employee", pk=emp.pk)


# ---------- letters ----------

@login_required
@require_perm("hr.edit")
def letter(request, pk, kind):
    emp = _emp(request, pk)
    cfg = HRSettings.for_tenant(request.tenant)
    if kind not in ("offer", "appointment"):
        raise Http404
    body = services.render_letter(cfg.offer_letter if kind == "offer" else cfg.appointment_letter, emp, request.tenant, cfg)
    audit(request, f"hr.letter_{kind}", emp)
    return render(request, "hr/letter.html", {"emp": emp, "kind": kind, "body": body, "title": "Offer letter" if kind == "offer" else "Appointment letter", "today": timezone.localdate()})
