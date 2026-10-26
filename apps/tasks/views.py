from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import audit
from apps.core.permissions import require_perm

from .forms import ProjectForm, QuickTaskForm, TaskForm
from .models import Project, Task
from .services import (assignable_users, can_edit_project, can_edit_task, can_manage_projects, notify_assigned, visible_projects, visible_tasks, week_end)


def _refresh():
    r = HttpResponse(status=204)
    r["HX-Refresh"] = "true"
    return r


def _err(form):
    return HttpResponse(f'<div class="field-err" role="alert">{"; ".join(e[0] for e in form.errors.values())}</div>', status=422)


def _task_or_404(request, pk) -> Task:
    return get_object_or_404(visible_tasks(request).select_related("project", "assignee", "lead", "company"), pk=pk)


# ---------- My tasks ----------

@login_required
@require_perm("tasks.view")
def my_tasks(request):
    today = timezone.localdate()
    who = request.GET.get("who", "me")
    status = request.GET.get("status", "open")
    project_id = request.GET.get("project") or ""
    q = (request.GET.get("q") or "").strip()
    qs = visible_tasks(request).select_related("project", "assignee", "lead", "company")
    if who == "me":
        qs = qs.filter(assignee=request.user)
    elif who == "unassigned":
        qs = qs.filter(assignee__isnull=True)
    elif who.isdigit():
        qs = qs.filter(assignee_id=int(who))
    if project_id.isdigit():
        qs = qs.filter(project_id=int(project_id))
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(company__name__icontains=q) | Q(lead__name__icontains=q))
    done = list(qs.filter(status=Task.Status.DONE).order_by("-done_at")[:30]) if status in ("done", "all") else []
    open_tasks = list(qs.exclude(status=Task.Status.DONE).order_by("due_date", "-priority", "-created_at")) if status in ("open", "all") else []
    we = week_end(today)
    groups = [("Overdue", [t for t in open_tasks if t.due_date and t.due_date < today], "red"),
              ("Today", [t for t in open_tasks if t.due_date == today], "brand"),
              ("This week", [t for t in open_tasks if t.due_date and today < t.due_date <= we], ""),
              ("Later", [t for t in open_tasks if t.due_date and t.due_date > we], ""),
              ("No date", [t for t in open_tasks if not t.due_date], "")]
    users = assignable_users(request)
    projects = visible_projects(request).exclude(status=Project.Status.DONE).order_by("name")
    ctx = {
        "groups": [g for g in groups if g[1]], "done": done, "open_count": len(open_tasks), "overdue_count": len(groups[0][1]),
        "who": who, "status": status, "project_id": project_id, "q": q, "users": users, "projects": projects, "today": today,
        "can_create": request.membership.has_perm("tasks.create"), "can_filter_people": len(users) > 1,
        "open_task": request.GET.get("open"),
    }
    return render(request, "tasks/my_tasks.html", ctx)


@login_required
@require_perm("tasks.create")
@require_POST
def quick_add(request):
    form = QuickTaskForm(request.POST)
    if not form.is_valid():
        return _err(form)
    d = form.cleaned_data
    users = {u.pk: u for u in assignable_users(request)}
    t = Task(title=d["title"].strip(), due_date=d.get("due_date"), assignee=users.get(d.get("assignee"), request.user))
    if d.get("project"):
        t.project = visible_projects(request).filter(pk=d["project"]).first()
    t.created_by = request.user
    t.save()
    audit(request, "task.create", t, after={"title": t.title, "assignee": t.assignee_id})
    notify_assigned(t, request.user)
    return _refresh()


@login_required
def task_form(request, pk=None):
    """Drawer: create (pk None) or edit. GET renders, POST saves."""
    task = _task_or_404(request, pk) if pk else None
    if task is None and not request.membership.has_perm("tasks.create"):
        raise PermissionDenied("You cannot create tasks.")
    if task is not None and not can_edit_task(request, task):
        raise PermissionDenied("You cannot edit this task.")
    users, projects = assignable_users(request), visible_projects(request).exclude(status=Project.Status.DONE)
    if request.method == "POST":
        form = TaskForm(request.POST, instance=task, users=users, projects=projects)
        if not form.is_valid():
            return _err(form)
        before = {"assignee": task.assignee_id, "status": task.status, "due": str(task.due_date)} if task else None
        t = form.save(commit=False)
        if t.lead_id and not t.company_id:
            t.company_id = t.lead.company_id
        if t.contract_id and not t.company_id:
            t.company_id = t.contract.company_id
        if t.status == Task.Status.DONE and not t.done_at:
            t.done_at = timezone.now()
        elif t.status != Task.Status.DONE:
            t.done_at = None
        if task is None:
            t.created_by = request.user
        t.save()
        audit(request, "task.update" if task else "task.create", t, before=before, after={"assignee": t.assignee_id, "status": t.status, "due": str(t.due_date)})
        if before is None or before["assignee"] != t.assignee_id:
            notify_assigned(t, request.user)
        return _refresh()
    initial = {"assignee": request.user.pk, "due_date": timezone.localdate()}
    for k in ("project", "lead", "company", "contract"):
        if request.GET.get(k, "").isdigit():
            initial[k] = int(request.GET[k])
    form = TaskForm(instance=task, users=users, projects=projects, initial=initial if task is None else None)
    return render(request, "tasks/_task_drawer.html", {"form": form, "task": task, "can_delete": task is not None})


@login_required
@require_POST
def toggle(request, pk):
    t = _task_or_404(request, pk)
    if not can_edit_task(request, t):
        raise PermissionDenied("You cannot change this task.")
    t.set_status(Task.Status.TODO if t.is_done else Task.Status.DONE)
    audit(request, "task.done" if t.is_done else "task.reopen", t)
    if request.htmx and request.headers.get("HX-Target", "").startswith("task-"):
        return render(request, "tasks/_task_row.html", {"t": t, "today": timezone.localdate()})
    return _refresh()


@login_required
@require_POST
def move(request, pk):
    """Board drag: POST stage=todo|doing|done (same contract as the lead kanban)."""
    t = _task_or_404(request, pk)
    if not can_edit_task(request, t):
        return JsonResponse({"error": "You cannot move this task."}, status=403)
    status = request.POST.get("stage") or request.POST.get("status")
    if status not in Task.Status.values:
        return JsonResponse({"error": "Unknown column."}, status=400)
    old = t.status
    t.set_status(status)
    audit(request, "task.move", t, before={"status": old}, after={"status": status})
    return JsonResponse({"ok": True, "status": status})


@login_required
@require_POST
def delete(request, pk):
    t = _task_or_404(request, pk)
    if not can_edit_task(request, t):
        raise PermissionDenied("You cannot delete this task.")
    t.soft_delete()
    audit(request, "task.delete", t, before={"title": t.title})
    messages.success(request, "Task deleted.")
    return _refresh()


# ---------- Projects ----------

@login_required
@require_perm("tasks.view")
def projects(request):
    status = request.GET.get("status", "active")
    qs = visible_projects(request).select_related("company", "owner").prefetch_related("members")
    if status != "all":
        qs = qs.filter(status=status)
    today = timezone.localdate()
    rows = []
    for p in qs:
        agg = p.tasks.aggregate(n=Count("id"), done=Count("id", filter=Q(status=Task.Status.DONE)), overdue=Count("id", filter=Q(due_date__lt=today) & ~Q(status=Task.Status.DONE)))
        pct = round(agg["done"] * 100 / agg["n"]) if agg["n"] else 0
        rows.append({"p": p, "n": agg["n"], "done": agg["done"], "overdue": agg["overdue"], "pct": pct})
    counts = {code: visible_projects(request).filter(status=code).count() for code, _ in Project.Status.choices}
    return render(request, "tasks/projects.html", {"rows": rows, "status": status, "counts": counts, "can_manage": can_manage_projects(request), "today": today})


@login_required
@require_perm("tasks.view")
def project_detail(request, pk):
    p = get_object_or_404(visible_projects(request).select_related("company", "owner").prefetch_related("members"), pk=pk)
    today = timezone.localdate()
    tasks = list(p.tasks.select_related("assignee").order_by("due_date", "-priority", "-created_at"))
    cols = [{"code": c, "label": l, "tasks": [t for t in tasks if t.status == c]} for c, l in Task.Status.choices]
    n, done = len(tasks), sum(1 for t in tasks if t.is_done)
    ctx = {
        "p": p, "cols": cols, "n": n, "done": done, "pct": round(done * 100 / n) if n else 0, "overdue": sum(1 for t in tasks if t.is_overdue),
        "can_edit": can_edit_project(request, p), "can_create": request.membership.has_perm("tasks.create"), "today": today,
        "can_move": request.membership.has_perm("tasks.create"),
    }
    return render(request, "tasks/project_detail.html", ctx)


@login_required
def project_form(request, pk=None):
    p = get_object_or_404(visible_projects(request), pk=pk) if pk else None
    if p is None and not can_manage_projects(request):
        raise PermissionDenied("Only managers and above create projects.")
    if p is not None and not can_edit_project(request, p):
        raise PermissionDenied("You cannot edit this project.")
    users = assignable_users(request)
    if request.method == "POST":
        form = ProjectForm(request.POST, instance=p, users=users)
        if not form.is_valid():
            return _err(form)
        obj = form.save(commit=False)
        if p is None:
            obj.created_by = request.user
        obj.save()
        form.save_m2m()
        audit(request, "project.update" if p else "project.create", obj, after={"name": obj.name, "status": obj.status, "members": list(obj.members.values_list("id", flat=True))})
        if p is None:
            r = HttpResponse(status=204)
            r["HX-Redirect"] = f"/tasks/projects/{obj.pk}/"
            return r
        return _refresh()
    form = ProjectForm(instance=p, users=users, initial={"owner": request.user.pk, "members": [request.user.pk], "start_date": timezone.localdate()} if p is None else None)
    return render(request, "tasks/_project_drawer.html", {"form": form, "p": p})


@login_required
@require_POST
def project_status(request, pk):
    p = get_object_or_404(visible_projects(request), pk=pk)
    if not can_edit_project(request, p):
        raise PermissionDenied("You cannot change this project.")
    status = request.POST.get("status")
    if status not in Project.Status.values:
        return HttpResponse("Unknown status", status=400)
    old = p.status
    p.status = status
    p.save(update_fields=["status", "updated_at"])
    audit(request, "project.status", p, before={"status": old}, after={"status": status})
    messages.success(request, f"{p.name} marked {p.get_status_display().lower()}.")
    return redirect("tasks:project", pk=p.pk)
