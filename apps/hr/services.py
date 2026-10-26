"""Who may see what, leave maths, attendance posting (self, field sessions, leave), monthly register, letters."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.core.money import rupees
from apps.core.permissions import has_perm

from .models import Attendance, Employee, Holiday, HRSettings, LeaveAdjustment, LeaveRequest, LeaveType


# ---------- access ----------

def my_employee(request) -> Employee | None:
    emp = getattr(request, "_hr_me", None)
    if emp is None and not hasattr(request, "_hr_me"):
        emp = Employee.objects.filter(user=request.user).select_related("department", "manager").first()
        request._hr_me = emp
    return emp


def can_see_sensitive(request) -> bool:
    """Bank details, ID numbers and salary: HR and the Owner only (section 9)."""
    return request.membership.role in ("hr", "owner")


def can_view_employee(request, emp: Employee) -> bool:
    if has_perm(request, "hr.view"):
        return True
    me = my_employee(request)
    return bool(me and (me.pk == emp.pk or emp.manager_id == me.pk))


def is_approver_for(request, emp: Employee) -> bool:
    if has_perm(request, "hr.edit"):
        return True
    me = my_employee(request)
    return bool(me and emp.manager_id == me.pk)


def approver_users(emp: Employee):
    """The employee's manager (if they have a login), else HR / Owners / Admins of the workspace."""
    from apps.accounts.models import Membership

    if emp.manager_id and emp.manager.user_id:
        return [emp.manager.user]
    return [m.user for m in Membership.objects.filter(tenant_id=emp.tenant_id, is_active=True, role__in=["hr", "owner", "admin"]).select_related("user")]


# ---------- calendar ----------

def fy_of(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_bounds(fy: int) -> tuple[date, date]:
    return date(fy, 4, 1), date(fy + 1, 3, 31)


def holidays_between(start: date, end: date) -> dict[date, Holiday]:
    return {h.date: h for h in Holiday.objects.filter(date__gte=start, date__lte=end, optional=False)}


def working_days(cfg: HRSettings, start: date, end: date, half_day=False) -> Decimal:
    """Days of leave consumed between two dates: week-offs and holidays are free."""
    if half_day:
        return Decimal("0.5")
    hol = holidays_between(start, end)
    n = 0
    d = start
    while d <= end:
        if not cfg.is_week_off(d) and d not in hol:
            n += 1
        d += timedelta(days=1)
    return Decimal(n)


# ---------- leave ----------

def taken(emp: Employee, lt: LeaveType, fy: int) -> Decimal:
    s, e = fy_bounds(fy)
    v = LeaveRequest.objects.filter(employee=emp, leave_type=lt, status=LeaveRequest.Status.APPROVED, start_date__gte=s, start_date__lte=e).aggregate(d=Sum("days"))["d"]
    return v or Decimal("0")


def pending_days(emp: Employee, lt: LeaveType, fy: int) -> Decimal:
    s, e = fy_bounds(fy)
    v = LeaveRequest.objects.filter(employee=emp, leave_type=lt, status=LeaveRequest.Status.PENDING, start_date__gte=s, start_date__lte=e).aggregate(d=Sum("days"))["d"]
    return v or Decimal("0")


def balances(emp: Employee, fy: int | None = None) -> list[dict]:
    fy = fy or fy_of(timezone.localdate())
    out = []
    for lt in LeaveType.objects.filter(is_active=True):
        adj = LeaveAdjustment.objects.filter(employee=emp, leave_type=lt, year=fy).aggregate(d=Sum("days"))["d"] or Decimal("0")
        allocated = lt.days_per_year + adj
        used = taken(emp, lt, fy)
        pend = pending_days(emp, lt, fy)
        out.append({"type": lt, "allocated": allocated, "used": used, "pending": pend, "available": allocated - used - pend, "pct": int(min(100, used * 100 / allocated)) if allocated else 0})
    return out


def apply_leave(emp: Employee, lt: LeaveType, start: date, end: date, half_day: bool, reason: str, by, cfg: HRSettings) -> LeaveRequest:
    days = working_days(cfg, start, end, half_day)
    if days <= 0:
        raise ValueError("Those dates are all week-offs or holidays.")
    if LeaveRequest.objects.filter(employee=emp, status__in=["pending", "approved"], start_date__lte=end, end_date__gte=start).exists():
        raise ValueError("You already have leave on those dates.")
    bal = next((b for b in balances(emp, fy_of(start)) if b["type"].pk == lt.pk), None)
    if bal and lt.paid and bal["available"] < days:
        raise ValueError(f"Only {bal['available']} {lt.name} day{'s' if bal['available'] != 1 else ''} left; this needs {days}.")
    lr = LeaveRequest(employee=emp, leave_type=lt, start_date=start, end_date=end, half_day=half_day, days=days, reason=reason.strip())
    lr.created_by = by
    lr.save()
    _notify_requested(lr)
    return lr


def decide_leave(lr: LeaveRequest, by, approve: bool, note: str = "", cfg: HRSettings | None = None):
    lr.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
    lr.decided_by, lr.decided_at, lr.decision_note = by, timezone.now(), note.strip()
    lr.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])
    if approve:
        cfg = cfg or HRSettings.for_tenant(lr.tenant)
        hol = holidays_between(lr.start_date, lr.end_date)
        for d in lr.dates():
            if cfg.is_week_off(d) or d in hol:
                continue
            Attendance.objects.update_or_create(employee=lr.employee, date=d, defaults={"status": Attendance.Status.HALF if lr.half_day else Attendance.Status.LEAVE, "source": Attendance.Source.LEAVE, "note": f"{lr.leave_type.name}", "tenant_id": lr.tenant_id})
    _notify_decided(lr)


def cancel_leave(lr: LeaveRequest, by):
    was_approved = lr.status == LeaveRequest.Status.APPROVED
    lr.status = LeaveRequest.Status.CANCELLED
    lr.decided_by, lr.decided_at = by, timezone.now()
    lr.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    if was_approved:
        Attendance.objects.filter(employee=lr.employee, date__gte=lr.start_date, date__lte=lr.end_date, source=Attendance.Source.LEAVE).delete()


def _notify_requested(lr: LeaveRequest):
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    url = reverse("hr:leave") + "?status=pending"
    for u in approver_users(lr.employee):
        notify(u, Notification.Kind.LEAVE, f"{lr.employee.first_name} asked for {lr.days:g} day{'s' if lr.days != 1 else ''} {lr.leave_type.name}", f"{lr.start_date:%d %b}{'' if lr.start_date == lr.end_date else ' to ' + lr.end_date.strftime('%d %b')}{' · ' + lr.reason if lr.reason else ''}", url,
               email_pref="email_approvals", email_subject=f"[QuantumLoop] Leave request: {lr.employee.name}", email_body=f"{lr.employee.name} asked for {lr.days:g} day(s) {lr.leave_type.name}, {lr.start_date:%d %b %Y} to {lr.end_date:%d %b %Y}.\n{lr.reason}\n\nDecide: {url}")


def _notify_decided(lr: LeaveRequest):
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    if not lr.employee.user_id:
        return
    verb = "approved" if lr.status == LeaveRequest.Status.APPROVED else "rejected"
    who = lr.decided_by.first_name if lr.decided_by else "HR"
    notify(lr.employee.user, Notification.Kind.LEAVE, f"{who} {verb} your {lr.leave_type.name} ({lr.start_date:%d %b})", lr.decision_note, reverse("hr:me"), email_pref="email_approvals",
           email_subject=f"[QuantumLoop] Leave {verb}: {lr.start_date:%d %b %Y}", email_body=f"Your {lr.leave_type.name} from {lr.start_date:%d %b %Y} to {lr.end_date:%d %b %Y} was {verb} by {who}.\n{lr.decision_note}")


# ---------- attendance ----------

def check_in(emp: Employee, when: datetime | None = None, source=Attendance.Source.SELF) -> Attendance:
    when = when or timezone.now()
    d = timezone.localtime(when).date()
    att, created = Attendance.objects.get_or_create(employee=emp, date=d, defaults={"check_in": when, "status": Attendance.Status.PRESENT, "source": source, "tenant_id": emp.tenant_id})
    if not created and att.check_in is None:
        att.check_in, att.status, att.source = when, Attendance.Status.PRESENT, source
        att.save(update_fields=["check_in", "status", "source", "updated_at"])
    return att


def check_out(emp: Employee, when: datetime | None = None, cfg: HRSettings | None = None) -> Attendance | None:
    when = when or timezone.now()
    d = timezone.localtime(when).date()
    att = Attendance.objects.filter(employee=emp, date=d).first()
    if att is None or att.check_in is None:
        return None
    att.check_out = when
    cfg = cfg or HRSettings.for_tenant(emp.tenant)
    if att.source != Attendance.Source.LEAVE and att.hours is not None and att.hours < cfg.half_day_hours:
        att.status = Attendance.Status.HALF
    att.save(update_fields=["check_out", "status", "updated_at"])
    return att


def today_for(emp: Employee) -> Attendance | None:
    return Attendance.objects.filter(employee=emp, date=timezone.localdate()).first()


def month_register(employees, year: int, month: int, cfg: HRSettings) -> dict:
    """Employee × day grid with codes and a payroll summary per employee."""
    today = timezone.localdate()
    ndays = calendar.monthrange(year, month)[1]
    days = [date(year, month, i) for i in range(1, ndays + 1)]
    hol = holidays_between(days[0], days[-1])
    rows = []
    att = {}
    for a in Attendance.objects.filter(employee__in=employees, date__gte=days[0], date__lte=days[-1]):
        att[(a.employee_id, a.date)] = a
    for e in employees:
        cells, s = [], {"present": 0, "half": 0, "absent": 0, "leave": 0, "wfh": 0, "holiday": 0, "weekoff": 0, "unmarked": 0}
        for d in days:
            a = att.get((e.pk, d))
            if d < e.join_date or (e.exit_date and d > e.exit_date):
                cells.append(("", "na"))
            elif a:
                cells.append((a.code, a.status))
                s[{"present": "present", "half_day": "half", "absent": "absent", "leave": "leave", "wfh": "wfh"}[a.status]] += 1
            elif d in hol:
                cells.append(("HO", "holiday"))
                s["holiday"] += 1
            elif cfg.is_week_off(d):
                cells.append(("WO", "weekoff"))
                s["weekoff"] += 1
            elif d <= today:
                cells.append(("-", "unmarked"))
                s["unmarked"] += 1
            else:
                cells.append(("", "future"))
        s["payable"] = Decimal(s["present"] + s["leave"] + s["wfh"] + s["holiday"] + s["weekoff"]) + Decimal(s["half"]) / 2
        rows.append({"employee": e, "cells": cells, "summary": s})
    return {"days": days, "rows": rows, "holidays": hol, "year": year, "month": month, "label": days[0].strftime("%B %Y")}


# ---------- letters ----------

def render_letter(template: str, emp: Employee, tenant, cfg: HRSettings | None = None) -> str:
    sal = emp.salary_paise
    ctx = {"name": emp.name, "designation": emp.designation or "—", "department": emp.department.name if emp.department else "—", "join_date": emp.join_date.strftime("%d %B %Y"),
           "salary": rupees(sal) + " per annum" if sal else "as agreed", "company": tenant.name, "manager": emp.manager.name if emp.manager else "your reporting manager", "code": emp.code, "today": timezone.localdate().strftime("%d %B %Y")}

    class Safe(dict):
        def __missing__(self, k):
            return "{" + k + "}"

    return template.format_map(Safe(ctx))
