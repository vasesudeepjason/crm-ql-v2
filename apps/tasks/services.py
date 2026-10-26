"""Visibility (own / team / all on tasks.view), edit rights, assignment notifications and the daily digest."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Membership
from apps.core.permissions import scope_for

from .models import Project, Task


def _ids(request):
    return request.membership.team_user_ids()


def visible_tasks(request, qs=None):
    qs = Task.objects.all() if qs is None else qs
    scope = scope_for(request, "tasks.view")
    if scope == "all":
        return qs
    if scope == "team":
        ids = _ids(request)
        return qs.filter(Q(assignee_id__in=ids) | Q(created_by_id__in=ids) | Q(project__members__in=ids) | Q(project__owner_id__in=ids)).distinct()
    if scope == "own":
        u = request.user.id
        return qs.filter(Q(assignee_id=u) | Q(created_by_id=u) | Q(project__members=u) | Q(project__owner_id=u)).distinct()
    return qs.none()


def visible_projects(request, qs=None):
    qs = Project.objects.all() if qs is None else qs
    scope = scope_for(request, "tasks.view")
    if scope == "all":
        return qs
    if scope == "team":
        ids = _ids(request)
        return qs.filter(Q(owner_id__in=ids) | Q(members__in=ids)).distinct()
    if scope == "own":
        u = request.user.id
        return qs.filter(Q(owner_id=u) | Q(members=u)).distinct()
    return qs.none()


def can_edit_task(request, task: Task) -> bool:
    scope = scope_for(request, "tasks.view")
    if scope == "all":
        return True
    u = request.user.id
    if u in (task.assignee_id, task.created_by_id):
        return True
    if scope == "team":
        ids = _ids(request)
        return task.assignee_id in ids or task.created_by_id in ids or (task.project_id and task.project.owner_id in ids)
    return bool(task.project_id and task.project.members.filter(pk=u).exists())


def can_manage_projects(request) -> bool:
    """Creating projects is a team-lead thing: Manager and above (team or all scope on tasks.create)."""
    return scope_for(request, "tasks.create") in ("team", "all")


def can_edit_project(request, project: Project) -> bool:
    scope = scope_for(request, "tasks.view")
    if scope == "all":
        return True
    if project.owner_id == request.user.id:
        return True
    return scope == "team" and project.owner_id in _ids(request)


def assignable_users(request):
    """Who a task can be given to: yourself, your team, or everyone, per tasks.create scope."""
    scope = scope_for(request, "tasks.create")
    qs = Membership.objects.filter(tenant=request.tenant, is_active=True).select_related("user").order_by("user__name")
    if scope == "all":
        return [m.user for m in qs]
    if scope == "team":
        return [m.user for m in qs.filter(user_id__in=_ids(request))]
    return [request.user]


def notify_assigned(task: Task, by):
    """In-app (and email, per preference) when a task is given to someone else."""
    if not task.assignee_id or task.assignee_id == by.id:
        return
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    due = f" · due {task.due_date.strftime('%d %b')}" if task.due_date else ""
    url = reverse("tasks:my") + f"?open={task.id}"
    notify(task.assignee, Notification.Kind.TASK_ASSIGNED, f"{by.first_name} assigned you a task: {task.title}", (task.project.name if task.project_id else "") + due, url,
           email_pref="email_tasks", email_subject=f"[QuantumLoop] Task for you: {task.title}",
           email_body=f"{by.name} assigned you a task.\n\n{task.title}{due}\n{task.description or ''}\n\nOpen My tasks: {url}")


def tasks_due_digest(tenant_id: int, today=None) -> int:
    """One notification per assignee with tasks overdue or due today. Returns how many people were told."""
    from apps.core.tenancy import tenant_context
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    today = today or timezone.localdate()
    sent = 0
    with tenant_context(tenant_id):
        rows = Task.objects.filter(due_date__lte=today, assignee__isnull=False).exclude(status=Task.Status.DONE).select_related("assignee", "project").order_by("assignee_id", "due_date")
        by_user: dict = {}
        for t in rows:
            by_user.setdefault(t.assignee, []).append(t)
        for user, tasks in by_user.items():
            overdue = [t for t in tasks if t.due_date < today]
            due = [t for t in tasks if t.due_date == today]
            title = f"{len(due)} task{'s' if len(due) != 1 else ''} due today" + (f", {len(overdue)} overdue" if overdue else "")
            body = "\n".join(f"• {t.title}{' · ' + t.project.name if t.project_id else ''} · {'overdue since ' + t.due_date.strftime('%d %b') if t.due_date < today else 'today'}" for t in tasks[:15])
            url = reverse("tasks:my")
            notify(user, Notification.Kind.TASKS_DUE, title, body, url, email_pref="email_reminders", email_subject=f"[QuantumLoop] {title}", email_body=f"Good morning {user.first_name},\n\n{title}:\n\n{body}\n\nOpen My tasks: {url}")
            sent += 1
    return sent


def week_end(today=None):
    today = today or timezone.localdate()
    return today + timedelta(days=6 - today.weekday())
