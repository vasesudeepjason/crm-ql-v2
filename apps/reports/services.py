"""
Report queries. Everything is scoped by the caller's `reports.view` scope (own / team /
all) and uses the Indian financial year (April–March) as the default long period.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from apps.accounts.models import Membership
from apps.approvals.models import Approval
from apps.crm.models import Activity, Lead, LeadStageHistory
from apps.field.models import Visit

STAGE_ORDER = [s for s, _ in Lead.Stage.choices]
FUNNEL = [("created", "Leads"), ("contacted", "Contacted"), ("qualified", "Qualified"), ("proposal", "Proposal sent"), ("won", "Won")]
REACHED = {  # a lead "reached" a funnel step if it is at that stage or any later one
    "contacted": STAGE_ORDER[STAGE_ORDER.index("contacted"):],
    "qualified": STAGE_ORDER[STAGE_ORDER.index("qualified"):],
    "proposal": STAGE_ORDER[STAGE_ORDER.index("proposal"):],
    "won": ["won"],
}


def fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def period(request):
    """(start_datetime, end_datetime, code, label) from ?period=7|30|90|fy|custom&from=&to=."""
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    code = request.GET.get("period", "30")
    if code == "custom" and request.GET.get("from"):
        try:
            s = date.fromisoformat(request.GET["from"])
            e = date.fromisoformat(request.GET.get("to") or today.isoformat())
        except ValueError:
            s, e, code = today - timedelta(days=29), today, "30"
        label = f"{s:%d %b %Y} – {e:%d %b %Y}"
    elif code == "fy":
        s, e = fy_start(today), today
        label = f"FY {s.year}-{str(s.year + 1)[2:]} to date"
    else:
        try:
            n = int(code)
        except ValueError:
            n, code = 30, "30"
        s, e = today - timedelta(days=n - 1), today
        label = f"Last {n} days"
    start = datetime.combine(s, time.min, tz)
    end = datetime.combine(e + timedelta(days=1), time.min, tz)
    return start, end, code, label, s, e


def team_users(request):
    """Users whose records the viewer may report on."""
    m = request.membership
    scope = m.scope_for("reports.view")
    qs = Membership.objects.filter(tenant=request.tenant, is_active=True).select_related("user")
    if scope == "team":
        qs = qs.filter(user_id__in=m.team_user_ids())
    elif scope != "all":
        qs = qs.filter(user_id=m.user_id)
    return [x.user for x in qs.order_by("user__name")]


def lead_scope(request, owner_id=None):
    users = team_users(request)
    qs = Lead.objects.filter(status=Lead.Status.ACTIVE, owner_id__in=[u.id for u in users])
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    return qs


# ---------- pipeline ----------

def pipeline(request, owner_id=None) -> dict:
    qs = lead_scope(request, owner_id)
    open_qs = qs.exclude(stage__in=["won", "lost"])
    by_stage = {r["stage"]: r for r in open_qs.values("stage").annotate(n=Count("id"), v=Sum("est_value_paise"))}
    stages = [{"code": c, "label": l, "n": by_stage.get(c, {}).get("n", 0), "v": by_stage.get(c, {}).get("v") or 0} for c, l in Lead.Stage.choices if c not in ("won", "lost")]
    total_n = sum(s["n"] for s in stages) or 1
    for s in stages:
        s["pct"] = round(s["n"] * 100 / total_n)
    by_source = list(open_qs.values("source").annotate(n=Count("id"), v=Sum("est_value_paise")).order_by("-n")[:10])
    by_owner = list(open_qs.values("owner__name").annotate(n=Count("id"), v=Sum("est_value_paise")).order_by("-v"))
    stale_cutoff = timezone.now() - timedelta(days=14)
    stale = open_qs.filter(updated_at__lt=stale_cutoff).count()
    overdue = open_qs.filter(next_follow_up__lt=timezone.localdate()).count()
    no_follow_up = open_qs.filter(next_follow_up__isnull=True).count()
    return {"stages": stages, "by_source": by_source, "by_owner": by_owner, "open_n": open_qs.count(), "open_v": open_qs.aggregate(v=Sum("est_value_paise"))["v"] or 0,
            "stale": stale, "overdue": overdue, "no_follow_up": no_follow_up}


# ---------- conversion ----------

def conversion(request, start, end, owner_id=None) -> dict:
    cohort = lead_scope(request, owner_id).filter(created_at__gte=start, created_at__lt=end)
    n = cohort.count()
    steps = [{"code": "created", "label": "Leads created", "n": n, "pct": 100 if n else 0}]
    for code, label in FUNNEL[1:]:
        k = cohort.filter(stage__in=REACHED[code]).count()
        steps.append({"code": code, "label": label, "n": k, "pct": round(k * 100 / n) if n else 0})
    won = cohort.filter(stage="won")
    lost = cohort.filter(stage="lost")
    won_n, lost_n = won.count(), lost.count()
    decided = won_n + lost_n
    cycle = won.exclude(won_at__isnull=True).annotate(d=ExpressionWrapper(F("won_at") - F("created_at"), output_field=DurationField())).aggregate(a=Avg("d"))["a"]
    by_source = []
    for r in cohort.values("source").annotate(n=Count("id"), won=Count("id", filter=Q(stage="won")), v=Sum("est_value_paise", filter=Q(stage="won"))).order_by("-n")[:10]:
        r["rate"] = round(r["won"] * 100 / r["n"]) if r["n"] else 0
        by_source.append(r)
    lost_reasons = list(lost.exclude(lost_reason="").values("lost_reason").annotate(n=Count("id")).order_by("-n")[:8])
    # all-time wins in the period (regardless of when the lead was created) — what the dashboard calls won revenue
    won_in_period = lead_scope(request, owner_id).filter(stage="won", won_at__gte=start, won_at__lt=end).aggregate(n=Count("id"), v=Sum("est_value_paise"))
    return {"steps": steps, "n": n, "won_n": won_n, "lost_n": lost_n, "win_rate": round(won_n * 100 / decided) if decided else None,
            "avg_days_to_win": round(cycle.total_seconds() / 86400, 1) if cycle else None, "by_source": by_source, "lost_reasons": lost_reasons,
            "won_in_period_n": won_in_period["n"] or 0, "won_in_period_v": won_in_period["v"] or 0}


# ---------- team ----------

def team(request, start, end) -> list[dict]:
    rows = []
    for u in team_users(request):
        leads = Lead.objects.filter(owner=u, status=Lead.Status.ACTIVE)
        created = leads.filter(created_at__gte=start, created_at__lt=end).count()
        won = leads.filter(stage="won", won_at__gte=start, won_at__lt=end).aggregate(n=Count("id"), v=Sum("est_value_paise"))
        calls = Activity.objects.filter(created_by=u, type="call", created_at__gte=start, created_at__lt=end).aggregate(n=Count("id"), talk=Sum("duration_seconds"))
        visits = Visit.objects.filter(executive=u, captured_at__gte=start, captured_at__lt=end).aggregate(n=Count("id"), ok=Count("id", filter=Q(status="approved")))
        subs = Approval.objects.filter(submitter=u, submitted_at__gte=start, submitted_at__lt=end).aggregate(n=Count("id"), ok=Count("id", filter=Q(status="approved")), rej=Count("id", filter=Q(status="rejected")))
        reviews = Approval.objects.filter(reviewer=u, decided_at__gte=start, decided_at__lt=end).count()
        open_n = leads.exclude(stage__in=["won", "lost"]).count()
        overdue = leads.exclude(stage__in=["won", "lost"]).filter(next_follow_up__lt=timezone.localdate()).count()
        rows.append({"user": u, "created": created, "won_n": won["n"] or 0, "won_v": won["v"] or 0, "calls": calls["n"] or 0, "talk_min": round((calls["talk"] or 0) / 60),
                     "visits": visits["n"] or 0, "visits_ok": visits["ok"] or 0, "subs": subs["n"] or 0, "subs_ok": subs["ok"] or 0, "subs_rej": subs["rej"] or 0, "reviews": reviews,
                     "open": open_n, "overdue": overdue})
    return rows


def activity_trend(request, start, end, owner_id=None) -> list[dict]:
    """Per-day counts for the period: leads created, calls, visits, wins."""
    days = (end.date() - start.date()).days
    if days > 92:
        return []
    tz = timezone.get_current_timezone()
    users = [u.id for u in team_users(request)] if not owner_id else [int(owner_id)]
    out = []
    for i in range(days):
        d0 = start + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        out.append({
            "day": d0.date(),
            "leads": Lead.objects.filter(owner_id__in=users, status="active", created_at__gte=d0, created_at__lt=d1).count(),
            "calls": Activity.objects.filter(created_by_id__in=users, type="call", created_at__gte=d0, created_at__lt=d1).count(),
            "visits": Visit.objects.filter(executive_id__in=users, captured_at__gte=d0, captured_at__lt=d1).count(),
            "won": Lead.objects.filter(owner_id__in=users, stage="won", won_at__gte=d0, won_at__lt=d1).count(),
        })
    return out
