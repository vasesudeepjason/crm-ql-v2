"""
What gets notified, to whom, and the email wording. Every function creates the in-app
notification first (always), then emails if the user's preference allows.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Membership
from apps.core.tenancy import get_current_tenant_id, tenant_context

from .dispatch import enqueue
from .models import Notification, NotificationPreference


def _link(path: str) -> str:
    return f"{settings.SITE_URL}{path}"


def _email(user, subject: str, body: str, note: Notification | None = None):
    try:
        send_mail(subject, body + f"\n\n— QuantumLoop\nManage email preferences: {_link(reverse('accounts:profile'))}", settings.DEFAULT_FROM_EMAIL, [user.email])
        if note is not None:
            note.emailed_at = timezone.now()
            note.save(update_fields=["emailed_at", "updated_at"])
    except Exception:  # never let mail trouble break the request or the job
        import logging

        logging.getLogger(__name__).exception("email failed")


def notify(user, kind: str, title: str, body: str = "", url: str = "", *, email_pref: str | None = None, email_subject: str | None = None, email_body: str | None = None) -> Notification:
    n = Notification(user=user, kind=kind, title=title, body=body[:500], url=url)
    n.created_by = None
    n.save()
    if email_pref and getattr(NotificationPreference.for_user(user), email_pref, False):
        _email(user, email_subject or title, email_body or f"{title}\n\n{body}\n\nOpen: {_link(url)}" if url else f"{title}\n\n{body}", n)
    return n


# ---------- approvals ----------

def reviewers_for(submitter, tenant_id) -> list:
    """The submitter's manager; else the workspace's owners and admins."""
    m = Membership.objects.filter(tenant_id=tenant_id, user=submitter, is_active=True).select_related("manager__user").first()
    if m and m.manager and m.manager.is_active:
        return [m.manager.user]
    return [x.user for x in Membership.objects.filter(tenant_id=tenant_id, is_active=True, role__in=["owner", "admin"]).select_related("user")]


def _approval_submitted_job(tenant_id: int, approval_id: int):
    from apps.approvals.models import Approval

    with tenant_context(tenant_id):
        a = Approval.objects.filter(pk=approval_id).select_related("submitter").first()
        if a is None:
            return
        url = reverse("approvals:queue") + f"?kind={a.kind}"
        for r in reviewers_for(a.submitter, tenant_id):
            notify(r, Notification.Kind.APPROVAL_SUBMITTED, f"{a.submitter.first_name} submitted a {a.get_kind_display().lower()}: {a.title}",
                   a.summary.get("note") or a.summary.get("outcome") or "", url, email_pref="email_approvals",
                   email_subject=f"[QuantumLoop] To review: {a.title}",
                   email_body=f"{a.submitter.name} submitted a {a.get_kind_display().lower()} for {a.company_name or a.title}.\n\n{a.summary.get('note', '')}\n\nReview it: {_link(url)}")


def _approval_decided_job(tenant_id: int, approval_id: int):
    from apps.approvals.models import Approval

    with tenant_context(tenant_id):
        a = Approval.objects.filter(pk=approval_id).select_related("submitter", "reviewer").first()
        if a is None:
            return
        verb = {"approved": "approved", "rejected": "rejected", "info": "needs more information on"}.get(a.status, a.status)
        who = a.reviewer.first_name if a.reviewer else "Your manager"
        if a.kind == "visit":
            url = reverse("field:my_day")
        else:
            url = reverse("crm:lead_detail", args=[a.object_id]) if a.object_id else reverse("telecaller:desk")
        title = f"{who} {verb} your {a.get_kind_display().lower()}: {a.title}"
        body = a.reason or ("It now counts in your numbers." if a.status == "approved" else "")
        notify(a.submitter, Notification.Kind.APPROVAL_DECIDED, title, body, url, email_pref="email_approvals",
               email_subject=f"[QuantumLoop] {a.get_kind_display()} {verb}: {a.title}",
               email_body=f"{title}.\n\n{('Reason: ' + a.reason) if a.reason else ''}\n\nOpen: {_link(url)}")


def approval_submitted(approval):
    enqueue(_approval_submitted_job, get_current_tenant_id() or approval.tenant_id, approval.id)


def approval_decided(approval):
    enqueue(_approval_decided_job, get_current_tenant_id() or approval.tenant_id, approval.id)


# ---------- daily digests ----------

def follow_up_digest(tenant_id: int, today=None) -> int:
    """One in-app + email per user with overdue and due-today follow-ups. Returns how many users were notified."""
    from apps.crm.models import Lead

    today = today or timezone.localdate()
    sent = 0
    with tenant_context(tenant_id):
        rows = Lead.objects.filter(status="active", next_follow_up__lte=today, owner__isnull=False).exclude(stage__in=["won", "lost"]).select_related("owner", "company").order_by("owner_id", "next_follow_up")
        by_user: dict = {}
        for l in rows:
            by_user.setdefault(l.owner, []).append(l)
        for user, leads in by_user.items():
            overdue = [l for l in leads if l.next_follow_up < today]
            due = [l for l in leads if l.next_follow_up == today]
            title = f"{len(due)} follow-up{'s' if len(due) != 1 else ''} due today" + (f", {len(overdue)} overdue" if overdue else "")
            lines = [f"• {l.name}{' · ' + l.company.name if l.company else ''} · {l.get_stage_display()} · {'overdue since ' + l.next_follow_up.strftime('%d %b') if l.next_follow_up < today else 'today'}" for l in leads[:15]]
            body = "\n".join(lines) + (f"\n…and {len(leads) - 15} more" if len(leads) > 15 else "")
            url = reverse("telecaller:desk") if Membership.objects.filter(user=user, tenant_id=tenant_id, role__in=["telecaller", "sales"]).exists() else reverse("crm:leads") + "?stage=follow_up"
            notify(user, Notification.Kind.FOLLOW_UPS, title, body, url, email_pref="email_reminders", email_subject=f"[QuantumLoop] {title}", email_body=f"Good morning {user.first_name},\n\n{title}:\n\n{body}\n\nOpen your queue: {_link(url)}")
            sent += 1
    return sent


def stale_approvals_digest(tenant_id: int) -> int:
    """Managers hear about items waiting more than two working days (section 5)."""
    from apps.approvals.models import Approval

    sent = 0
    with tenant_context(tenant_id):
        stale = [a for a in Approval.objects.filter(status__in=["pending", "info"]).select_related("submitter") if a.is_stale]
        if not stale:
            return 0
        by_reviewer: dict = {}
        for a in stale:
            for r in reviewers_for(a.submitter, tenant_id):
                by_reviewer.setdefault(r, []).append(a)
        for user, items in by_reviewer.items():
            title = f"{len(items)} approval{'s' if len(items) != 1 else ''} waiting more than 2 working days"
            body = "\n".join(f"• {a.get_kind_display()} · {a.title} · {a.submitter.first_name} · {a.working_days_waiting} working days" for a in items[:15])
            url = reverse("approvals:queue")
            notify(user, Notification.Kind.STALE_APPROVALS, title, body, url, email_pref="email_reminders", email_subject=f"[QuantumLoop] {title}", email_body=f"{title}:\n\n{body}\n\nReview them: {_link(url)}")
            sent += 1
    return sent


def unread_count(user, tenant_id) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).count()
