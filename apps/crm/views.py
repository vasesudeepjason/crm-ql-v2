from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.approvals.models import Approval
from apps.core.audit import audit
from apps.core.permissions import has_perm, require_perm

from .duplicates import find_duplicates
from .forms import ActivityForm, LeadForm, StageForm
from .models import Activity, Company, Lead, LeadStageHistory
from .scoping import scoped

SOURCES = ["Referral", "Telecalling", "Field visit", "IndiaMART", "JustDial", "Website", "Walk-in", "WhatsApp", "Exhibition"]


# ---------- lists ----------

@login_required
@require_perm("leads.view")
def lead_list(request):
    qs = scoped(request, Lead.objects.select_related("company", "owner"), "leads.view")
    stage = request.GET.get("stage", "")
    q = request.GET.get("q", "").strip()
    if stage:
        qs = qs.filter(stage=stage)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(company__name__icontains=q) | Q(phone_normalized__icontains=q))
    counts = dict(scoped(request, Lead.objects.all(), "leads.view").values_list("stage").annotate(n=Count("id")))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    ctx = {"page": page, "stage": stage, "q": q, "stages": Lead.Stage.choices, "counts": counts, "total": sum(counts.values())}
    if request.htmx:
        return render(request, "crm/_lead_rows.html", ctx)
    return render(request, "crm/leads.html", ctx)


@login_required
@require_perm("companies.view")
def company_list(request):
    qs = scoped(request, Company.objects.select_related("owner").annotate(lead_count=Count("leads")).order_by("name"), "companies.view")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(gstin__icontains=q))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "crm/companies.html", {"page": page, "q": q})


# ---------- create / edit ----------

def _visible_lead(request, pk):
    return get_object_or_404(scoped(request, Lead.objects.select_related("company", "owner"), "leads.view"), pk=pk)


def _can_edit(request, lead) -> bool:
    """leads.edit within scope; a telecaller may edit their own lead only while it is pending or rejected."""
    m = request.membership
    if has_perm(request, "leads.edit"):
        scope = m.scope_for("leads.edit")
        return scope == "all" or (scope == "team" and lead.owner_id in m.team_user_ids()) or (scope == "own" and lead.owner_id == m.user_id)
    return lead.created_by_id == m.user_id and lead.status in (Lead.Status.PENDING, Lead.Status.REJECTED)


@login_required
@require_perm("leads.create")
def lead_create(request):
    initial = {}
    if request.GET.get("from_call"):
        initial = {"source": "Telecalling", "phone": request.GET.get("phone", ""), "consent_note": "Captured during an outbound call; agreed to follow-up"}
    form = LeadForm(request.POST or None, request=request, initial=initial)
    dups = []
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        dups = find_duplicates(phone=d.get("phone", ""), email=d.get("email", ""), name=d.get("name", ""))
        if dups and not d.get("create_anyway"):
            messages.warning(request, "This looks like an existing lead. Open one of the matches, or tick 'Create anyway'.")
        else:
            lead = form.save(commit=False)
            needs_approval = request.membership.role == "telecaller"
            lead.status = Lead.Status.PENDING if needs_approval else Lead.Status.ACTIVE
            lead.save()
            LeadStageHistory.objects.create(lead=lead, from_stage="", to_stage=lead.stage, changed_by=request.user, note="created")
            if d.get("first_call_outcome"):
                call = Activity(lead=lead, company=lead.company, type=Activity.Type.CALL, outcome=d["first_call_outcome"], notes=d.get("first_call_notes", ""))
                call.created_by = request.user
                call.save()
            if needs_approval:
                Approval.objects.create(kind=Approval.Kind.LEAD, content_type=ContentType.objects.get_for_model(Lead), object_id=lead.id, submitter=request.user,
                                        title=lead.name, company_name=lead.company.name if lead.company else "", value_paise=lead.est_value_paise,
                                        summary={"outcome": dict(Activity.Outcome.choices).get(d.get("first_call_outcome"), lead.get_stage_display()), "note": d.get("first_call_notes") or d.get("consent_note", "")})
            audit(request, "lead.create", lead, after={"name": lead.name, "stage": lead.stage, "status": lead.status, "duplicates_overridden": len(dups)})
            messages.success(request, f"Lead '{lead.name}' {'submitted for approval' if needs_approval else 'created'}.")
            return redirect("crm:lead_detail", pk=lead.pk)
    return render(request, "crm/lead_form.html", {"form": form, "dups": dups, "sources": SOURCES, "is_new": True, "needs_approval": request.membership.role == "telecaller", "from_call": bool(request.GET.get("from_call") or request.POST.get("first_call_outcome"))})


@login_required
def lead_edit(request, pk):
    lead = _visible_lead(request, pk)
    if not _can_edit(request, lead):
        raise PermissionDenied("You cannot edit this lead.")
    form = LeadForm(request.POST or None, request=request, instance=lead)
    if request.method == "POST" and form.is_valid():
        before = {"name": lead.name, "phone": lead.phone, "stage": lead.stage, "owner": lead.owner_id, "est_value_paise": lead.est_value_paise, "company": lead.company_id}
        old_stage = lead.stage
        lead = form.save()
        if lead.stage != old_stage:
            LeadStageHistory.objects.create(lead=lead, from_stage=old_stage, to_stage=lead.stage, changed_by=request.user, note="edited")
            if lead.stage == Lead.Stage.WON:
                lead.won_at = timezone.now()
                lead.save(update_fields=["won_at"])
        audit(request, "lead.edit", lead, before=before, after={"name": lead.name, "phone": lead.phone, "stage": lead.stage, "owner": lead.owner_id, "est_value_paise": lead.est_value_paise, "company": lead.company_id})
        messages.success(request, "Lead updated.")
        return redirect("crm:lead_detail", pk=lead.pk)
    return render(request, "crm/lead_form.html", {"form": form, "lead": lead, "dups": [], "sources": SOURCES, "is_new": False})


@login_required
@require_GET
@require_perm("leads.view")
def check_duplicate(request):
    exclude = request.GET.get("exclude")
    dups = find_duplicates(phone=request.GET.get("phone", ""), email=request.GET.get("email", ""), name=request.GET.get("name", ""), exclude_id=int(exclude) if exclude else None)
    return render(request, "crm/_duplicates.html", {"dups": dups, "live": True})


# ---------- detail ----------

@login_required
@require_perm("leads.view")
def lead_detail(request, pk):
    lead = _visible_lead(request, pk)
    activities = list(lead.activities.select_related("created_by").order_by("-created_at")[:100])
    history = list(lead.history.select_related("changed_by").order_by("-created_at")[:50])
    approvals = list(Approval.objects.filter(content_type=ContentType.objects.get_for_model(Lead), object_id=lead.id).select_related("submitter", "reviewer"))
    timeline = sorted(
        [*({"kind": "activity", "at": a.created_at, "obj": a} for a in activities),
         *({"kind": "stage", "at": h.created_at, "obj": h} for h in history),
         *({"kind": "approval", "at": a.decided_at or a.submitted_at, "obj": a} for a in approvals)],
        key=lambda x: x["at"], reverse=True,
    )
    ctx = {
        "lead": lead, "timeline": timeline, "can_edit": _can_edit(request, lead), "approval": next((a for a in approvals if a.is_open), None),
        "stage_form": StageForm(initial={"stage": lead.stage, "next_follow_up": lead.next_follow_up}), "activity_form": ActivityForm(initial={"type": "call"}),
        "open_tasks": [a for a in activities if a.type == Activity.Type.TASK and a.done_at is None],
        "dups": find_duplicates(phone=lead.phone, email=lead.email, exclude_id=lead.id, limit=3),
    }
    return render(request, "crm/lead_detail.html", ctx)


@login_required
@require_POST
def lead_stage(request, pk):
    lead = _visible_lead(request, pk)
    if not _can_edit(request, lead):
        raise PermissionDenied("You cannot change this lead's stage.")
    form = StageForm(request.POST)
    if not form.is_valid():
        return HttpResponse(f'<div class="field-err" role="alert">{"; ".join(e[0] for e in form.errors.values())}</div>', status=422)
    d = form.cleaned_data
    try:
        old = lead.stage
        if d["stage"] == Lead.Stage.LOST:
            lead.lost_reason = d["lost_reason"].strip()
        if d.get("next_follow_up"):
            lead.next_follow_up = d["next_follow_up"]
        lead.move_to(d["stage"], request.user, d.get("note") or d.get("lost_reason") or "")
    except ValueError as e:
        return HttpResponse(f'<div class="field-err" role="alert">{e}</div>', status=422)
    audit(request, "lead.stage", lead, before={"stage": old}, after={"stage": lead.stage, "note": d.get("note", "")})
    if request.htmx:
        resp = HttpResponse(status=204)
        resp["HX-Refresh"] = "true"
        return resp
    return redirect("crm:lead_detail", pk=lead.pk)


@login_required
@require_POST
def activity_add(request, pk):
    lead = _visible_lead(request, pk)
    perm = "calls.create" if request.POST.get("type") == "call" else "tasks.create"
    if not (has_perm(request, perm) or _can_edit(request, lead)):
        raise PermissionDenied("You cannot log activity on this lead.")
    form = ActivityForm(request.POST)
    if not form.is_valid():
        return HttpResponse(f'<div class="field-err" role="alert">{"; ".join(e[0] for e in form.errors.values())}</div>', status=422)
    a = form.save(commit=False)
    a.lead, a.company = lead, lead.company
    if form.cleaned_data.get("duration_minutes") is not None:
        a.duration_seconds = form.cleaned_data["duration_minutes"] * 60
    a.created_by = request.user
    a.save()
    if form.cleaned_data.get("next_follow_up"):
        lead.next_follow_up = form.cleaned_data["next_follow_up"]
        lead.save(update_fields=["next_follow_up", "updated_at"])
    if a.type == Activity.Type.CALL and a.outcome == Activity.Outcome.INTERESTED and lead.stage in (Lead.Stage.NEW, Lead.Stage.ATTEMPTED, Lead.Stage.CONTACTED):
        lead.move_to(Lead.Stage.INTERESTED, request.user, "call outcome")
    elif a.type == Activity.Type.CALL and lead.stage == Lead.Stage.NEW:
        lead.move_to(Lead.Stage.CONTACTED if a.outcome == Activity.Outcome.CONNECTED else Lead.Stage.ATTEMPTED, request.user, "call logged")
    audit(request, "activity.add", a, after={"type": a.type, "outcome": a.outcome, "lead": lead.id})
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    return resp


@login_required
@require_POST
def task_done(request, pk, activity_id):
    lead = _visible_lead(request, pk)
    a = get_object_or_404(Activity, pk=activity_id, lead=lead)
    a.done_at = timezone.now()
    a.save(update_fields=["done_at", "updated_at"])
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    return resp


# ---------- kanban ----------

KANBAN_LIMIT = 40


@login_required
@require_perm("leads.view")
def kanban(request):
    qs = scoped(request, Lead.objects.filter(status=Lead.Status.ACTIVE).select_related("company", "owner"), "leads.view")
    owner = request.GET.get("owner", "")
    q = request.GET.get("q", "").strip()
    if owner:
        qs = qs.filter(owner_id=owner)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(company__name__icontains=q))
    today = timezone.localdate()
    columns = []
    for code, label in Lead.Stage.choices:
        col_qs = qs.filter(stage=code).order_by("-updated_at")
        agg = col_qs.aggregate(n=Count("id"), v=Sum("est_value_paise"))
        columns.append({"code": code, "label": label, "n": agg["n"] or 0, "value": agg["v"] or 0, "leads": list(col_qs[:KANBAN_LIMIT]), "more": max(0, (agg["n"] or 0) - KANBAN_LIMIT)})
    from .forms import owner_choices

    return render(request, "crm/kanban.html", {"columns": columns, "today": today, "owner": owner, "q": q, "owners": owner_choices(request), "can_move": has_perm(request, "leads.edit")})


@login_required
@require_POST
def kanban_move(request, pk):
    lead = _visible_lead(request, pk)
    if not _can_edit(request, lead):
        return JsonResponse({"ok": False, "error": "You cannot move this lead."}, status=403)
    stage = request.POST.get("stage")
    if stage not in Lead.Stage.values:
        return JsonResponse({"ok": False, "error": "Unknown stage."}, status=400)
    if stage == lead.stage:
        return JsonResponse({"ok": True})
    if stage == Lead.Stage.LOST and not request.POST.get("reason"):
        return JsonResponse({"ok": False, "needs_reason": True}, status=422)
    old = lead.stage
    try:
        if stage == Lead.Stage.LOST:
            lead.lost_reason = request.POST.get("reason", "")
        lead.move_to(stage, request.user, request.POST.get("reason", "") or "kanban")
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=422)
    audit(request, "lead.stage", lead, before={"stage": old}, after={"stage": stage, "via": "kanban"})
    return JsonResponse({"ok": True, "stage": stage})
