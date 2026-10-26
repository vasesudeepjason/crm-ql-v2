"""Audit-log viewer (section 9: an append-only log of approvals, exports, role changes and support access)."""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import Membership

from .models import AuditLog
from .permissions import require_perm

DAYS = [("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days"), ("all", "All time")]
GROUPS = [("", "Everything"), ("approval", "Approvals"), ("auth", "Sign-ins & security"), ("role", "Roles"), ("user", "Users"), ("invite", "Invitations"), ("import", "Imports"), ("export", "Exports"), ("contract", "Contracts"), ("product", "Catalogue"), ("invoice", "Invoices"), ("payment", "Payments"), ("hr", "HR"), ("leave", "Leave"), ("attendance", "Attendance"), ("task", "Tasks"), ("project", "Projects"), ("support", "Support sessions")]


@login_required
@require_perm("audit.view")
def audit_log(request):
    qs = AuditLog.objects.filter(tenant=request.tenant).select_related("actor")
    group = request.GET.get("group", "")
    actor = request.GET.get("actor", "")
    days = request.GET.get("days", "30")
    q = request.GET.get("q", "").strip()
    if group:
        qs = qs.filter(action__startswith=group + ".")
    if actor:
        qs = qs.filter(actor_id=actor)
    if days and days != "all":
        qs = qs.filter(at__gte=timezone.now() - timedelta(days=int(days)))
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(model__icontains=q) | Q(object_id=q) | Q(note__icontains=q) | Q(actor__name__icontains=q))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    ctx = {"page": page, "groups": GROUPS, "day_choices": DAYS, "group": group, "actor": actor, "days": days, "q": q, "section": "audit",
           "actors": Membership.objects.filter(tenant=request.tenant).select_related("user").order_by("user__name")}
    if request.htmx:
        return render(request, "settings/_audit_rows.html", ctx)
    return render(request, "settings/audit.html", ctx)
