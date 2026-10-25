from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, F, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Membership
from apps.approvals.models import Approval
from apps.core.audit import audit
from apps.core.permissions import has_perm, require_perm
from apps.crm.models import Company, Lead
from apps.crm.scoping import scoped

from . import services
from .forms import VisitForm
from .models import FieldSession, Visit


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


# ---------- executive ----------

@login_required
@require_perm("visits.view")
def my_day(request):
    services.close_stale_sessions(request.tenant.timezone)
    if request.GET.get("saved"):
        messages.success(request, "Visit submitted for approval.")
    elif request.GET.get("queued"):
        messages.info(request, "No network — the visit is saved on your phone and will be sent automatically when you're back online.")
    session = services.active_session(request.user)
    today = timezone.localdate()
    start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    visits = list(Visit.objects.filter(executive=request.user, captured_at__gte=start).select_related("company", "lead"))
    planned = list(Lead.objects.filter(owner=request.user, next_follow_up__lte=today, company__isnull=False).exclude(stage__in=["won", "lost"]).select_related("company").order_by("next_follow_up")[:12])
    pending = Approval.objects.filter(kind="visit", submitter=request.user, status__in=["pending", "info"]).count()
    rejected = Visit.objects.filter(executive=request.user, status="rejected", captured_at__gte=start - timedelta(days=7)).count()
    ctx = {"session": session, "visits": visits, "planned": planned, "pending": pending, "rejected": rejected, "today": today, "can_create": has_perm(request, "visits.create")}
    return render(request, "field/my_day.html", ctx)


@login_required
@require_perm("visits.create")
@require_POST
def session_start(request):
    if services.active_session(request.user):
        return redirect("field:my_day")
    s = FieldSession(executive=request.user, start_lat=_f(request.POST.get("lat")), start_lng=_f(request.POST.get("lng")), start_accuracy=_f(request.POST.get("accuracy")), device=request.META.get("HTTP_USER_AGENT", "")[:160])
    s.last_lat, s.last_lng, s.last_seen_at = s.start_lat, s.start_lng, timezone.now()
    s.created_by = request.user
    s.save()
    audit(request, "field.session_start", s, after={"lat": s.start_lat, "lng": s.start_lng})
    messages.success(request, "Day started. Location is recorded only while your day is active.")
    return redirect("field:my_day")


@login_required
@require_POST
def session_end(request):
    s = services.active_session(request.user)
    if s:
        s.end(FieldSession.EndSource.MANUAL, _f(request.POST.get("lat")), _f(request.POST.get("lng")))
        audit(request, "field.session_end", s, after={"source": "manual"})
        messages.success(request, f"Day ended · {int(s.duration.total_seconds() // 3600)} h {int(s.duration.total_seconds() % 3600 // 60)} min on the road.")
    return redirect("field:my_day")


@login_required
@require_POST
def ping(request):
    """Lightweight position update while a session is active; silently ignored otherwise."""
    s = services.active_session(request.user)
    lat, lng = _f(request.POST.get("lat")), _f(request.POST.get("lng"))
    if s and lat is not None and lng is not None:
        s.ping(lat, lng)
        return JsonResponse({"ok": True, "active": True})
    return JsonResponse({"ok": True, "active": False})


@login_required
@require_perm("visits.create")
def visit_new(request):
    is_sync = request.headers.get("X-Offline-Sync") == "1"
    session = services.active_session(request.user)
    if session is None and not is_sync:
        messages.warning(request, "Start your day before recording a visit.")
        return redirect("field:my_day")
    initial = {}
    if request.GET.get("lead"):
        lead = Lead.objects.filter(pk=request.GET["lead"], owner=request.user).first()
        if lead:
            initial = {"lead": lead, "company": lead.company}
    form = VisitForm(request.POST or None, request.FILES or None, request=request, initial=initial)
    if request.method == "POST" and is_sync and not form.is_valid():
        return JsonResponse({"ok": False, "permanent": True, "errors": {k: [str(e) for e in v] for k, v in form.errors.items()}}, status=400)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        existing = Visit.objects.filter(executive=request.user, offline_id=d["offline_id"]).first() if d.get("offline_id") else None
        if existing:
            if is_sync:
                return JsonResponse({"ok": True, "id": existing.id, "duplicate": True})
            messages.info(request, "That visit was already saved.")
            return redirect("field:my_day")
        v = form.save(commit=False)
        if d.get("captured_at"):
            v.captured_at = d["captured_at"]
        if session is None:
            session = services.session_for(request.user, v.captured_at)
        v.session, v.executive = session, request.user
        v.lat, v.lng, v.accuracy, v.mock_location = d.get("lat"), d.get("lng"), d.get("accuracy"), bool(d.get("mock_location"))
        v.offline_id = d.get("offline_id", "")
        if d.get("new_company") and not v.company:
            c = Company(name=d["new_company"].strip(), owner=request.user, source="Field visit")
            c.created_by = request.user
            c.save()
            v.company = c
        if v.lead and not v.company:
            v.company = v.lead.company
        v.created_by = request.user
        v.save()
        if v.lat is not None:
            session.ping(v.lat, v.lng)
        ap = services.submit_visit(v)
        audit(request, "visit.submit", v, after={"outcome": v.outcome, "company": v.company_id, "accuracy": v.accuracy, "approval": ap.id, "synced": is_sync})
        if is_sync:
            return JsonResponse({"ok": True, "id": v.id, "title": v.title})
        messages.success(request, f"Visit to {v.title} submitted for approval.")
        return redirect("field:my_day")
    return render(request, "field/visit_form.html", {"form": form, "session": session})


@login_required
@require_perm("visits.view")
def my_visits(request):
    qs = scoped(request, Visit.objects.select_related("company", "lead", "executive"), "visits.view", owner_field="executive_id")
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    return render(request, "field/visits.html", {"visits": qs[:200], "status": status})


@login_required
def visit_photo(request, pk):
    """Photos live in private storage; this view checks who may see them (section 9)."""
    v = get_object_or_404(scoped(request, Visit.objects.all(), "visits.view", owner_field="executive_id") if has_perm(request, "visits.view") else Visit.objects.none(), pk=pk)
    if not v.photo:
        raise Http404
    resp = FileResponse(v.photo.open("rb"), content_type="image/jpeg")
    resp["Cache-Control"] = "private, max-age=300"
    return resp


# ---------- manager ----------

def _team_users(request):
    m = request.membership
    qs = Membership.objects.filter(tenant=request.tenant, is_active=True, role="field").select_related("user")
    if m.scope_for("visits.approve") == "team":
        qs = qs.filter(user_id__in=m.team_user_ids())
    return [x.user for x in qs.order_by("user__name")]


@login_required
@require_perm("visits.approve")
def team(request):
    services.close_stale_sessions(request.tenant.timezone)
    execs = _team_users(request)
    ids = [u.id for u in execs]
    now = timezone.now()
    today = timezone.localdate()
    start_today = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    days = int(request.GET.get("days", "7") or 7)
    since = now - timedelta(days=days)
    live = {s.executive_id: s for s in FieldSession.objects.filter(executive_id__in=ids, ended_at__isnull=True)}
    today_counts = dict(Visit.objects.filter(executive_id__in=ids, captured_at__gte=start_today).values_list("executive_id").annotate(n=Count("id")))
    perf_rows = []
    for u in execs:
        vs = Visit.objects.filter(executive=u, captured_at__gte=since)
        agg = vs.aggregate(n=Count("id"), approved=Count("id", filter=Q(status="approved")), rejected=Count("id", filter=Q(status="rejected")), pending=Count("id", filter=Q(status="pending")), low=Count("id", filter=Q(accuracy__gt=250)))
        decided = Approval.objects.filter(kind="visit", submitter=u, decided_at__isnull=False, submitted_at__gte=since).annotate(wait=F("decided_at") - F("submitted_at")).aggregate(avg=Avg("wait"))["avg"]
        sessions = FieldSession.objects.filter(executive=u, started_at__gte=since).count()
        perf_rows.append({"user": u, "live": live.get(u.id), "today": today_counts.get(u.id, 0), "sessions": sessions, "avg_review_h": round(decided.total_seconds() / 3600, 1) if decided else None, **agg})
    queue = Approval.objects.filter(kind="visit", status__in=["pending", "info"], submitter_id__in=ids)
    stale = sum(1 for a in queue if a.is_stale)
    ctx = {"rows": perf_rows, "live_count": sum(1 for r in perf_rows if r["live"]), "queue_count": queue.count(), "stale": stale, "days": days, "today": today}
    return render(request, "field/team.html", ctx)


@login_required
@require_perm("visits.approve")
@require_POST
def end_session_for(request, pk):
    s = get_object_or_404(FieldSession, pk=pk, executive_id__in=[u.id for u in _team_users(request)])
    s.end(FieldSession.EndSource.MANAGER)
    audit(request, "field.session_end", s, after={"source": "manager"})
    messages.success(request, f"Ended {s.executive.name}'s day.")
    return redirect("field:team")
