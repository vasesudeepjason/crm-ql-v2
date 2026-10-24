from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.core.audit import audit

from .models import Approval, ApprovalError

TABS = [
    ("pending", "Pending", Q(status=Approval.Status.PENDING)),
    ("info", "Info requested", Q(status=Approval.Status.INFO)),
    ("approved", "Approved", Q(status=Approval.Status.APPROVED)),
    ("rejected", "Rejected", Q(status=Approval.Status.REJECTED)),
    ("all", "All", Q()),
]


def _visible(request):
    """A manager sees their team's submissions; owner/admin see all; others see their own."""
    m = request.membership
    qs = Approval.objects.select_related("submitter", "reviewer")
    if m.role in ("owner", "admin", "viewer"):
        return qs
    if m.role == "manager":
        return qs.filter(submitter_id__in=m.team_user_ids())
    return qs.filter(submitter_id=m.user_id)


def _can_review(request):
    m = request.membership
    return m.has_perm("leads.approve") or m.has_perm("visits.approve")


def _filtered(request, base):
    """Kind / executive / date filters shared by the queue and bulk approve (section 4: filters by executive and date)."""
    kind = request.GET.get("kind", "")
    who = request.GET.get("who", "")
    d_from = request.GET.get("from", "")
    d_to = request.GET.get("to", "")
    if kind in ("lead", "visit"):
        base = base.filter(kind=kind)
    if who.isdigit():
        base = base.filter(submitter_id=int(who))
    if d_from:
        base = base.filter(submitted_at__date__gte=d_from)
    if d_to:
        base = base.filter(submitted_at__date__lte=d_to)
    return base, {"kind": kind, "who": who, "from": d_from, "to": d_to}


@login_required
def queue(request):
    tab = request.GET.get("tab", "pending")
    q = request.GET.get("q", "").strip()
    base, filters = _filtered(request, _visible(request))
    counts = {code: base.filter(cond).count() for code, _, cond in TABS}
    cond = next((c for code, _, c in TABS if code == tab), TABS[0][2])
    qs = base.filter(cond)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(company_name__icontains=q) | Q(submitter__name__icontains=q))
    submitters = _visible(request).values("submitter_id", "submitter__name").distinct().order_by("submitter__name")
    qs_str = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    ctx = {"items": qs[:100], "tab": tab, "q": q, "tabs": [(c, n, counts[c]) for c, n, _ in TABS], "can_review": _can_review(request), "filters": filters, "submitters": submitters, "qs_str": qs_str}
    if request.htmx:
        return render(request, "approvals/_table.html", ctx)
    return render(request, "approvals/queue.html", ctx)


@login_required
def detail(request, pk):
    a = get_object_or_404(_visible(request), pk=pk)
    siblings = list(_visible(request).filter(status=a.status).values_list("id", flat=True))
    i = siblings.index(a.id) if a.id in siblings else -1
    ctx = {
        "a": a,
        "rec": a.record,
        "prev_id": siblings[i - 1] if i > 0 else None,
        "next_id": siblings[i + 1] if 0 <= i < len(siblings) - 1 else None,
        "decidable": a.can_decide(request.membership),
    }
    return render(request, "approvals/_drawer.html", ctx)


def _decide(request, pk, action):
    a = get_object_or_404(_visible(request), pk=pk)
    before = {"status": a.status}
    try:
        if action == "approve":
            a.approve(request.membership)
        elif action == "reject":
            a.reject(request.membership, request.POST.get("reason", ""))
        else:
            a.request_info(request.membership, request.POST.get("reason", ""))
    except ApprovalError as e:
        return HttpResponse(f'<div class="field-err" role="alert">{e}</div>', status=422)
    audit(request, f"approval.{action}", a, before=before, after={"status": a.status, "reason": a.reason})
    resp = render(request, "approvals/_row.html", {"a": a, "can_review": _can_review(request)})
    resp["HX-Trigger"] = "approval-decided"
    return resp


@login_required
@require_POST
def bulk_approve(request):
    """Approve many at once (section 4: bulk approve). Each item still passes the same rules."""
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    done, skipped = 0, []
    for a in _visible(request).filter(pk__in=ids):
        try:
            a.approve(request.membership)
            audit(request, "approval.approve", a, before={"status": "pending"}, after={"status": a.status, "bulk": True})
            done += 1
        except ApprovalError as e:
            skipped.append(f"{a.title}: {e}")
    from django.contrib import messages
    from django.shortcuts import redirect

    if done:
        messages.success(request, f"Approved {done} item{'s' if done != 1 else ''}.")
    if skipped:
        messages.warning(request, "Skipped — " + "; ".join(skipped[:5]) + (" …" if len(skipped) > 5 else ""))
    return redirect(request.POST.get("next") or "approvals:queue")


@login_required
@require_POST
def approve(request, pk):
    return _decide(request, pk, "approve")


@login_required
@require_POST
def reject(request, pk):
    return _decide(request, pk, "reject")


@login_required
@require_POST
def request_info(request, pk):
    return _decide(request, pk, "info")
