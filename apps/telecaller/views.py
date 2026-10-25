from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Membership
from apps.core.audit import audit
from apps.core.permissions import has_perm, require_perm
from apps.crm.duplicates import find_duplicates
from apps.crm.forms import ActivityForm
from apps.crm.models import Activity, Lead
from apps.crm.scoping import scoped

from . import services
from .models import CallTarget


# ---------- telecaller ----------

@login_required
@require_perm("calls.view")
def desk(request):
    start, end = services.day_bounds()
    queue = services.call_queue(request.user)
    recent = Activity.objects.filter(created_by=request.user, type=Activity.Type.CALL).select_related("lead", "lead__company").order_by("-created_at")[:15]
    progress = services.today_progress(request.user)
    pending_leads = Lead.objects.filter(created_by=request.user, status=Lead.Status.PENDING).count()
    ctx = {"queue": queue, "recent": recent, "p": progress, "pending_leads": pending_leads, "today": timezone.localdate(), "can_log": has_perm(request, "calls.create"), "can_create": has_perm(request, "leads.create")}
    return render(request, "telecaller/desk.html", ctx)


@login_required
@require_perm("calls.create")
def log_form(request, pk):
    """Drawer: the quick call-log form for one lead, with its last few calls."""
    lead = get_object_or_404(scoped(request, Lead.objects.select_related("company", "owner"), "leads.view"), pk=pk)
    last = lead.activities.filter(type=Activity.Type.CALL).select_related("created_by").order_by("-created_at")[:5]
    form = ActivityForm(initial={"type": "call", "next_follow_up": lead.next_follow_up})
    return render(request, "telecaller/_log_drawer.html", {"lead": lead, "last": last, "form": form, "stages": [c for c in Lead.Stage.choices if c[0] not in ("won",)]})


@login_required
@require_perm("calls.create")
@require_POST
def log_call(request, pk):
    lead = get_object_or_404(scoped(request, Lead.objects.all(), "leads.view"), pk=pk)
    form = ActivityForm({**request.POST.dict(), "type": "call"})
    if not form.is_valid():
        return HttpResponse(f'<div class="field-err" role="alert">{"; ".join(e[0] for e in form.errors.values())}</div>', status=422)
    a = form.save(commit=False)
    a.lead, a.company, a.type = lead, lead.company, Activity.Type.CALL
    if form.cleaned_data.get("duration_minutes") is not None:
        a.duration_seconds = form.cleaned_data["duration_minutes"] * 60
    a.created_by = request.user
    a.save()
    changed = []
    if form.cleaned_data.get("next_follow_up"):
        lead.next_follow_up = form.cleaned_data["next_follow_up"]
        changed.append("next_follow_up")
    elif a.outcome in (Activity.Outcome.NO_ANSWER, Activity.Outcome.BUSY) and not lead.next_follow_up:
        lead.next_follow_up = timezone.localdate() + timedelta(days=1)  # try again tomorrow
        changed.append("next_follow_up")
    if changed:
        lead.save(update_fields=changed + ["updated_at"])
    stage = request.POST.get("stage")
    if stage and stage in Lead.Stage.values and stage != lead.stage:
        if stage == Lead.Stage.LOST:
            lead.lost_reason = request.POST.get("lost_reason") or a.get_outcome_display()
        lead.move_to(stage, request.user, "call desk")
    elif a.outcome == Activity.Outcome.INTERESTED and lead.stage in (Lead.Stage.NEW, Lead.Stage.ATTEMPTED, Lead.Stage.CONTACTED):
        lead.move_to(Lead.Stage.INTERESTED, request.user, "call outcome")
    elif a.outcome == Activity.Outcome.NOT_INTERESTED and lead.stage not in (Lead.Stage.WON, Lead.Stage.LOST):
        lead.lost_reason = "Not interested (call)"
        lead.move_to(Lead.Stage.LOST, request.user, "call outcome")
    elif lead.stage == Lead.Stage.NEW:
        lead.move_to(Lead.Stage.CONTACTED if a.outcome in services.CONNECTED else Lead.Stage.ATTEMPTED, request.user, "call logged")
    elif a.outcome == Activity.Outcome.WRONG_NUMBER:
        lead.do_not_contact = True
        lead.save(update_fields=["do_not_contact", "updated_at"])
    audit(request, "call.log", a, after={"lead": lead.id, "outcome": a.outcome, "duration": a.duration_seconds})
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    return resp


# ---------- manager ----------

def _team(request):
    m = request.membership
    qs = Membership.objects.filter(tenant=request.tenant, is_active=True, role__in=["telecaller", "sales"]).select_related("user")
    if m.scope_for("leads.assign") == "team":
        qs = qs.filter(user_id__in=m.team_user_ids())
    elif m.scope_for("leads.assign") != "all":
        qs = qs.none()
    return [x.user for x in qs.order_by("user__name")]


@login_required
@require_perm("leads.assign")
def team(request):
    users = _team(request)
    days = int(request.GET.get("days", "7") or 7)
    now = timezone.now()
    since = now - timedelta(days=days)
    start_today, end_today = services.day_bounds()
    targets = {t.user_id: t for t in CallTarget.objects.filter(user__in=users)}
    rows = []
    for u in users:
        today = services.stats_for(u, start_today, end_today)
        period = services.stats_for(u, since, now)
        t = targets.get(u.id)
        working_days = max(1, sum(1 for i in range(days) if (now - timedelta(days=i)).weekday() < 6))
        rows.append({"user": u, "today": today, "period": period, "target": t,
                     "calls_att": round(period["calls"] * 100 / (t.calls_per_day * working_days)) if t and t.calls_per_day else None,
                     "leads_att": round(period["leads"] * 100 / (t.leads_per_day * working_days)) if t and t.leads_per_day else None})
    totals = {"calls": sum(r["period"]["calls"] for r in rows), "leads": sum(r["period"]["leads"] for r in rows), "approved": sum(r["period"]["leads_approved"] for r in rows)}
    return render(request, "telecaller/team.html", {"rows": rows, "days": days, "totals": totals, "pending": sum(r["period"]["leads_pending"] for r in rows)})


@login_required
@require_perm("leads.assign")
@require_POST
def set_target(request, user_id):
    users = {u.id: u for u in _team(request)}
    if user_id not in users:
        raise PermissionDenied("Not in your team.")
    try:
        calls, leads = int(request.POST.get("calls_per_day", 0)), int(request.POST.get("leads_per_day", 0))
    except ValueError:
        messages.error(request, "Targets must be whole numbers.")
        return redirect("telecaller:team")
    t, created = CallTarget.objects.get_or_create(user=users[user_id], defaults={"calls_per_day": calls, "leads_per_day": leads, "set_by": request.user, "created_by": request.user})
    before = None if created else {"calls": t.calls_per_day, "leads": t.leads_per_day}
    if not created:
        t.calls_per_day, t.leads_per_day, t.set_by = calls, leads, request.user
        t.save(update_fields=["calls_per_day", "leads_per_day", "set_by", "updated_at"])
    audit(request, "target.set", t, before=before, after={"calls": calls, "leads": leads, "user": user_id})
    messages.success(request, f"Targets for {users[user_id].name}: {calls} calls, {leads} leads a day.")
    return redirect("telecaller:team")
