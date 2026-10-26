import json
import zoneinfo
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.approvals.models import Approval
from apps.crm.models import Activity, Lead
from apps.crm.scoping import scoped
from apps.tasks.models import Task
from apps.contracts import services as contract_services
from apps.invoices import services as invoice_services

PHASES = {
    "field": ("Field sales", "Phase 1"), "telecaller": ("Telecaller", "Phase 1"), "contracts": ("Contracts & renewals", "Phase 2"),
    "invoices": ("Invoices & payments", "Phase 2"), "hr": ("HR", "Phase 4"), "tasks": ("Tasks & projects", "Phase 1"),
    "reports": ("Reports", "Phase 1"), "setup": ("Set up", "Phase 0"),
}


def _tenant_now(request):
    tz = zoneinfo.ZoneInfo(request.tenant.timezone or "Asia/Kolkata")
    return timezone.now().astimezone(tz), tz


def _bounds(d: date, tz):
    return datetime.combine(d, time.min, tz), datetime.combine(d + timedelta(days=1), time.min, tz)


def _pct(cur, prev):
    if not prev:
        return None
    return round((cur - prev) / prev * 100)


@login_required
def home(request):
    now, tz = _tenant_now(request)
    today = now.date()
    yday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    leads = scoped(request, Lead.objects.filter(status=Lead.Status.ACTIVE), "leads.view")
    calls = scoped(request, Activity.objects.filter(type=Activity.Type.CALL), "calls.view", owner_field="created_by_id")

    def count_between(qs, field, start, end):
        s, _ = _bounds(start, tz)
        _, e = _bounds(end, tz)
        return qs.filter(**{f"{field}__gte": s, f"{field}__lt": e}).count()

    def sum_between(qs, field, start, end):
        s, _ = _bounds(start, tz)
        _, e = _bounds(end, tz)
        return qs.filter(**{f"{field}__gte": s, f"{field}__lt": e}).aggregate(v=Sum("est_value_paise"))["v"] or 0

    won = leads.filter(stage=Lead.Stage.WON)
    kpis = {
        "leads": {"today": count_between(leads, "created_at", today, today), "yday": count_between(leads, "created_at", yday, yday),
                  "month": count_between(leads, "created_at", month_start, today), "last": count_between(leads, "created_at", last_month_start, last_month_end)},
        "calls": {"today": count_between(calls, "created_at", today, today), "yday": count_between(calls, "created_at", yday, yday),
                  "month": count_between(calls, "created_at", month_start, today), "last": count_between(calls, "created_at", last_month_start, last_month_end)},
        "won": {"today": sum_between(won, "won_at", today, today), "yday": sum_between(won, "won_at", yday, yday),
                "month": sum_between(won, "won_at", month_start, today), "last": sum_between(won, "won_at", last_month_start, last_month_end)},
    }
    for k in kpis.values():
        k["delta"] = _pct(k["today"], k["yday"])

    # Dynamics: daily won revenue this month vs last month
    def daily(start, end):
        s, _ = _bounds(start, tz)
        _, e = _bounds(end, tz)
        rows = won.filter(won_at__gte=s, won_at__lt=e).annotate(d=TruncDate("won_at", tzinfo=tz)).values("d").annotate(v=Sum("est_value_paise"))
        return {r["d"]: r["v"] or 0 for r in rows}

    this_days = monthrange(today.year, today.month)[1]
    last_days = monthrange(last_month_start.year, last_month_start.month)[1]
    cur_map, last_map = daily(month_start, today), daily(last_month_start, last_month_end)
    chart = {
        "month": today.strftime("%b"),
        "today": today.day,
        "this": [cur_map.get(month_start + timedelta(days=i), 0) / 100 if i < today.day else None for i in range(this_days)],
        "last": [last_map.get(last_month_start + timedelta(days=i), 0) / 100 if i < last_days else 0 for i in range(this_days)],
    }

    # Approvals visible to this user
    pending = Approval.objects.filter(status__in=[Approval.Status.PENDING, Approval.Status.INFO]).select_related("submitter")
    m = request.membership
    if m.role == "manager":
        pending = pending.filter(submitter_id__in=m.team_user_ids())
    elif m.role not in ("owner", "admin", "viewer"):
        pending = pending.filter(submitter_id=m.user_id)
    pending_list = list(pending[:6])
    stale = sum(1 for a in pending if a.is_stale)

    due = scoped(request, Lead.objects.filter(status=Lead.Status.ACTIVE, next_follow_up__lte=today).exclude(stage__in=[Lead.Stage.WON, Lead.Stage.LOST]).select_related("company", "owner"), "leads.view")
    overdue = due.filter(next_follow_up__lt=today).count()
    follow_ups = list(due.order_by("next_follow_up")[:6])

    pipeline = dict(leads.exclude(stage__in=[Lead.Stage.WON, Lead.Stage.LOST]).values_list("stage").annotate(n=Count("id")))
    my_targets = None
    if m.has_perm("calls.create") and m.role in ("telecaller", "sales"):
        from apps.telecaller.services import today_progress

        my_targets = today_progress(request.user)

    my_tasks = list(Task.objects.filter(assignee=request.user, due_date__lte=today).exclude(status=Task.Status.DONE).select_related("project").order_by("due_date")[:4]) if m.has_perm("tasks.view") else []
    renewals = []
    renewal_risk = 0
    if m.has_perm("contracts.view"):
        base = contract_services.scoped_contracts(request)
        renewals = list(base.filter(status="active", end_date__lte=today + timedelta(days=30)).order_by("end_date")[:5])
        renewal_risk = contract_services.at_risk_summary(base, today)["risk"]
    inv_summary, overdue_invoices = None, []
    if m.has_perm("invoices.view"):
        ibase = invoice_services.scoped_invoices(request)
        inv_summary = invoice_services.outstanding_summary(ibase, today)
        overdue_invoices = list(ibase.filter(status__in=["issued", "part_paid"], due_date__lt=today).order_by("due_date")[:5])
    return render(request, "dashboard/home.html", {
        "inv_summary": inv_summary, "overdue_invoices": overdue_invoices, "renewals": renewals, "renewal_risk": renewal_risk, "my_tasks": my_tasks, "now": now, "kpis": kpis, "chart_json": json.dumps(chart),
        "pending": pending_list, "pending_total": pending.count(), "stale": stale,
        "follow_ups": follow_ups, "due_total": due.count(), "overdue": overdue, "pipeline": pipeline, "open_leads": sum(pipeline.values()),
        "can_review": m.has_perm("leads.approve") or m.has_perm("visits.approve"), "my_targets": my_targets,
    })


@login_required
def coming_soon(request, module):
    name, phase = PHASES.get(module, (module.title(), "later"))
    return render(request, "dashboard/soon.html", {"name": name, "phase": phase})


def offline(request):
    """Shown by the service worker when a page isn't cached and there's no network."""
    return render(request, "offline.html")


def service_worker(request):
    """Served from the root so its scope covers the whole app (static/ would limit it to /static/)."""
    from django.contrib.staticfiles import finders
    from django.http import FileResponse, Http404

    path = finders.find("sw.js")
    if not path:
        raise Http404
    resp = FileResponse(open(path, "rb"), content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


def healthz(request):
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return JsonResponse({"ok": True, "db": connection.vendor})
