def _renewals_due(request, m):
    if m is None or not m.has_perm("contracts.view"):
        return 0
    from datetime import timedelta

    from django.utils import timezone

    from apps.contracts.services import scoped_contracts

    return scoped_contracts(request).filter(status="active", end_date__lte=timezone.localdate() + timedelta(days=30)).count()


def tenant_context(request):
    m = getattr(request, "membership", None)
    badge = 0
    if m is not None and (m.has_perm("leads.approve") or m.has_perm("visits.approve")):
        from apps.approvals.models import Approval

        qs = Approval.objects.filter(status__in=[Approval.Status.PENDING, Approval.Status.INFO])
        if m.role == "manager":
            qs = qs.filter(submitter_id__in=m.team_user_ids())
        badge = qs.count()
    from django.utils import timezone

    return {
        "today": timezone.localdate(),
        "can_review": bool(m and (m.has_perm("leads.approve") or m.has_perm("visits.approve"))),
        "tenant": getattr(request, "tenant", None),
        "membership": m,
        "perms_set": m.permissions() if m else frozenset(),
        "pending_badge": badge,
        "renewals_due": _renewals_due(request, m),
        "tasks_due": __import__("apps.tasks.models", fromlist=["Task"]).Task.objects.filter(assignee=request.user, due_date__lte=timezone.localdate()).exclude(status="done").count() if m is not None and m.has_perm("tasks.view") else 0,
        "unread_count": __import__("apps.notifications.models", fromlist=["Notification"]).Notification.objects.filter(user=request.user, read_at__isnull=True).count() if m is not None else 0,
    }
