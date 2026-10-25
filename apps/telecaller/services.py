"""Call-desk queries shared by the desk, the team page and the dashboard."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.approvals.models import Approval
from apps.crm.models import Activity, Lead

from .models import CallTarget

CONNECTED = (Activity.Outcome.CONNECTED, Activity.Outcome.INTERESTED, Activity.Outcome.CALLBACK, Activity.Outcome.NOT_INTERESTED)


def day_bounds(day=None):
    day = day or timezone.localdate()
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
    return start, start + timedelta(days=1)


def call_queue(user, limit=50):
    """Who to call next: overdue and due follow-ups first, then untouched new leads, then the rest by last touch."""
    today = timezone.localdate()
    base = Lead.objects.filter(owner=user, status=Lead.Status.ACTIVE, do_not_contact=False).exclude(stage__in=[Lead.Stage.WON, Lead.Stage.LOST]).select_related("company")
    due = list(base.filter(next_follow_up__lte=today).order_by("next_follow_up", "-updated_at")[:limit])
    seen = {l.id for l in due}
    fresh = [l for l in base.filter(stage=Lead.Stage.NEW).annotate(n=Count("activities", filter=Q(activities__type="call"))).filter(n=0).order_by("created_at")[:limit] if l.id not in seen]
    seen |= {l.id for l in fresh}
    rest = [l for l in base.filter(Q(next_follow_up__isnull=True) | Q(next_follow_up__gt=today)).order_by("updated_at")[: limit * 2] if l.id not in seen]
    out = due + fresh + rest
    for l in due:
        l.reason = "overdue" if l.next_follow_up < today else "due today"
    for l in fresh:
        l.reason = "new · never called"
    for l in rest:
        l.reason = f"follow-up {l.next_follow_up.day} {l.next_follow_up:%b}" if l.next_follow_up else "no follow-up set"
    return out[:limit]


def stats_for(user, start, end) -> dict:
    calls = Activity.objects.filter(created_by=user, type=Activity.Type.CALL, created_at__gte=start, created_at__lt=end)
    agg = calls.aggregate(n=Count("id"), connected=Count("id", filter=Q(outcome__in=CONNECTED)), talk=Sum("duration_seconds"), interested=Count("id", filter=Q(outcome=Activity.Outcome.INTERESTED)))
    leads = Approval.objects.filter(kind=Approval.Kind.LEAD, submitter=user, submitted_at__gte=start, submitted_at__lt=end)
    lagg = leads.aggregate(n=Count("id"), approved=Count("id", filter=Q(status=Approval.Status.APPROVED)), rejected=Count("id", filter=Q(status=Approval.Status.REJECTED)), pending=Count("id", filter=Q(status__in=[Approval.Status.PENDING, Approval.Status.INFO])))
    direct = Lead.objects.filter(created_by=user, status=Lead.Status.ACTIVE, created_at__gte=start, created_at__lt=end).exclude(id__in=Approval.objects.filter(kind="lead", submitter=user).values("object_id")).count()
    return {
        "calls": agg["n"] or 0, "connected": agg["connected"] or 0, "interested": agg["interested"] or 0, "talk_minutes": round((agg["talk"] or 0) / 60),
        "connect_rate": round((agg["connected"] or 0) * 100 / agg["n"]) if agg["n"] else None,
        "leads": (lagg["n"] or 0) + direct, "leads_approved": (lagg["approved"] or 0) + direct, "leads_rejected": lagg["rejected"] or 0, "leads_pending": lagg["pending"] or 0,
    }


def target_for(user) -> CallTarget | None:
    return CallTarget.objects.filter(user=user).first()


def today_progress(user) -> dict:
    start, end = day_bounds()
    s = stats_for(user, start, end)
    t = target_for(user)
    s["target_calls"] = t.calls_per_day if t else None
    s["target_leads"] = t.leads_per_day if t else None
    s["calls_pct"] = min(100, round(s["calls"] * 100 / t.calls_per_day)) if t and t.calls_per_day else None
    s["leads_pct"] = min(100, round(s["leads"] * 100 / t.leads_per_day)) if t and t.leads_per_day else None
    return s
