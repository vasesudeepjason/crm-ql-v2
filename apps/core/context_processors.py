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
        "unread_count": __import__("apps.notifications.models", fromlist=["Notification"]).Notification.objects.filter(user=request.user, read_at__isnull=True).count() if m is not None else 0,
    }
