from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.core.tenancy import tenant_context
from apps.notifications.models import Notification
from apps.tasks.models import Project, Task
from apps.tasks.services import tasks_due_digest


def _mk(two_tenants, **kw):
    with tenant_context(two_tenants["a"].id):
        t = Task(**kw)
        t.created_by = kw.get("assignee") or two_tenants["owner_a"]
        t.save()
        return t


def test_my_tasks_scope_and_groups(two_tenants, login, client):
    today = timezone.localdate()
    tc, other, owner = two_tenants["tc_a"], two_tenants["other_tc"], two_tenants["owner_a"]
    _mk(two_tenants, title="Mine overdue", assignee=tc, due_date=today - timedelta(days=1))
    _mk(two_tenants, title="Mine today", assignee=tc, due_date=today)
    _mk(two_tenants, title="Theirs", assignee=other, due_date=today)
    _mk(two_tenants, title="Done one", assignee=tc, due_date=today, status="done", done_at=timezone.now())
    client = login(tc)
    r = client.get(reverse("tasks:my"))
    assert r.status_code == 200
    body = r.content.decode()
    assert "Mine overdue" in body and "Mine today" in body and "Theirs" not in body and "Done one" not in body
    assert "Overdue" in body and "1 overdue" in body
    # telecaller (own scope) cannot see other people's tasks even with who=all
    assert b"Theirs" not in client.get(reverse("tasks:my"), {"who": "all"}).content
    assert b"Done one" in client.get(reverse("tasks:my"), {"status": "done"}).content
    client.logout()
    # manager sees the team's, not the stranger's
    client = login(two_tenants["mgr_a"])
    body = client.get(reverse("tasks:my"), {"who": "all"}).content.decode()
    assert "Mine today" in body and "Theirs" not in body
    client.logout()
    client = login(owner)
    assert b"Theirs" in client.get(reverse("tasks:my"), {"who": "all"}).content
    client.logout()
    client = login(two_tenants["owner_b"])
    assert b"Mine today" not in client.get(reverse("tasks:my"), {"who": "all"}).content


def test_quick_add_assign_notifies_and_toggle(two_tenants, login, client):
    mgr, tc = two_tenants["mgr_a"], two_tenants["tc_a"]
    client = login(mgr)
    mail.outbox.clear()
    r = client.post(reverse("tasks:quick_add"), {"title": "Call the distributor", "due_date": timezone.localdate(), "assignee": tc.id})
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    with tenant_context(two_tenants["a"].id):
        t = Task.objects.get(title="Call the distributor")
        assert t.assignee == tc and t.created_by == mgr
        n = Notification.objects.get(user=tc, kind="task_assigned")
        assert "Mgr assigned you a task" in n.title and n.url.endswith(f"?open={t.id}")
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [tc.email]
    # assignee can mark it done via the row toggle, which returns the row
    client.logout()
    client = login(tc)
    r = client.post(reverse("tasks:toggle", args=[t.id]), HTTP_HX_REQUEST="true", HTTP_HX_TARGET=f"task-{t.id}")
    assert r.status_code == 200 and b"tick on" in r.content
    with tenant_context(two_tenants["a"].id):
        t.refresh_from_db()
        assert t.is_done and t.done_at is not None
    # a stranger in the tenant cannot touch it
    client.logout()
    client = login(two_tenants["other_tc"])
    assert client.post(reverse("tasks:toggle", args=[t.id])).status_code == 404


def test_task_drawer_create_edit_delete(two_tenants, login, client):
    owner = two_tenants["owner_a"]
    client = login(owner)
    r = client.get(reverse("tasks:new"), {"lead": two_tenants["lead_a"].id})
    assert r.status_code == 200 and b"Create task" in r.content
    r = client.post(reverse("tasks:new"), {"title": "Send proposal", "assignee": owner.id, "due_date": timezone.localdate(), "priority": "high", "status": "todo", "lead": two_tenants["lead_a"].id})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        t = Task.objects.get(title="Send proposal")
        assert t.lead == two_tenants["lead_a"] and t.company == two_tenants["lead_a"].company  # company inferred from the lead
    # lead detail shows it as an open task and in the timeline
    body = client.get(reverse("crm:lead_detail", args=[two_tenants["lead_a"].id])).content.decode()
    assert "Send proposal" in body and "Open tasks" in body
    # validation error comes back as 422
    assert client.post(reverse("tasks:edit", args=[t.id]), {"title": "", "status": "todo", "priority": "normal"}).status_code == 422
    r = client.post(reverse("tasks:edit", args=[t.id]), {"title": "Send proposal v2", "status": "done", "priority": "normal"})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        t.refresh_from_db()
        assert t.title == "Send proposal v2" and t.is_done and t.done_at
    assert client.post(reverse("tasks:delete", args=[t.id])).status_code == 204
    with tenant_context(two_tenants["a"].id):
        assert not Task.objects.filter(pk=t.id).exists() and Task.all_objects.filter(pk=t.id, is_deleted=True).exists()


def test_lead_detail_add_task_creates_task(two_tenants, login, client):
    client = login(two_tenants["owner_a"])
    due = timezone.now() + timedelta(days=1)
    r = client.post(reverse("crm:activity_add", args=[two_tenants["lead_a"].id]), {"type": "task", "notes": "Chase the PO", "due_at": due.strftime("%Y-%m-%dT%H:%M")})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        t = Task.objects.get(title="Chase the PO")
        assert t.lead == two_tenants["lead_a"] and t.assignee == two_tenants["owner_a"]
    assert client.post(reverse("crm:task_done", args=[two_tenants["lead_a"].id, t.id])).status_code == 204
    with tenant_context(two_tenants["a"].id):
        t.refresh_from_db()
        assert t.is_done


def test_projects_create_board_move_and_permissions(two_tenants, login, client):
    mgr, tc, other = two_tenants["mgr_a"], two_tenants["tc_a"], two_tenants["other_tc"]
    # a telecaller may not create projects
    client = login(tc)
    assert client.get(reverse("tasks:project_new")).status_code == 403
    client.logout()
    client = login(mgr)
    r = client.post(reverse("tasks:project_new"), {"name": "Onboarding", "owner": mgr.id, "members": [mgr.id, tc.id], "status": "active", "color": "green", "start_date": "2026-10-01", "due_date": "2026-09-01"})
    assert r.status_code == 422  # due before start
    r = client.post(reverse("tasks:project_new"), {"name": "Onboarding", "owner": mgr.id, "members": [mgr.id, tc.id], "status": "active", "color": "green"})
    assert r.status_code == 204 and r["HX-Redirect"].startswith("/tasks/projects/")
    with tenant_context(two_tenants["a"].id):
        p = Project.objects.get(name="Onboarding")
        assert set(p.members.values_list("id", flat=True)) == {mgr.id, tc.id}
    r = client.post(reverse("tasks:new"), {"title": "Kick-off", "assignee": tc.id, "status": "todo", "priority": "normal", "project": p.id})
    assert r.status_code == 204
    with tenant_context(two_tenants["a"].id):
        t = Task.objects.get(title="Kick-off")
    body = client.get(reverse("tasks:project", args=[p.id])).content.decode()
    assert "Kick-off" in body and 'data-stage="doing"' in body and "0/1 done" in body
    # drag to done
    r = client.post(reverse("tasks:move", args=[t.id]), {"stage": "done"})
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert "1/1 done" in client.get(reverse("tasks:project", args=[p.id])).content.decode()
    assert client.post(reverse("tasks:move", args=[t.id]), {"stage": "nope"}).status_code == 400
    # member sees the project; a non-member telecaller does not
    client.logout()
    client = login(tc)
    assert client.get(reverse("tasks:project", args=[p.id])).status_code == 200
    assert b"Onboarding" in client.get(reverse("tasks:projects")).content
    client.logout()
    client = login(other)
    assert client.get(reverse("tasks:project", args=[p.id])).status_code == 404
    # mark completed
    client.logout()
    client = login(mgr)
    assert client.post(reverse("tasks:project_status", args=[p.id]), {"status": "done"}).status_code == 302
    with tenant_context(two_tenants["a"].id):
        p.refresh_from_db()
        assert p.status == "done"


def test_tasks_due_digest_and_sidebar_count(two_tenants, login, client):
    tc = two_tenants["tc_a"]
    today = timezone.localdate()
    _mk(two_tenants, title="Late", assignee=tc, due_date=today - timedelta(days=2))
    _mk(two_tenants, title="Now", assignee=tc, due_date=today)
    _mk(two_tenants, title="Later", assignee=tc, due_date=today + timedelta(days=3))
    mail.outbox.clear()
    assert tasks_due_digest(two_tenants["a"].id) == 1
    with tenant_context(two_tenants["a"].id):
        n = Notification.objects.get(user=tc, kind="tasks_due")
        assert n.title == "1 task due today, 1 overdue" and "Later" not in n.body
    assert any("task due today" in m.subject for m in mail.outbox)
    client = login(tc)
    body = client.get(reverse("dashboard:home")).content.decode()
    assert "My tasks" in body and "Late" in body and "Later" not in body
    assert '<em class="count" style="font-style:normal">2</em>' in body
