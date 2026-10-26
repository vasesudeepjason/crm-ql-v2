"""Tasks logged on leads before this module existed were Activity rows of type 'task'. Move them over."""
from django.db import migrations


def forward(apps, schema_editor):
    Activity = apps.get_model("crm", "Activity")
    Task = apps.get_model("tasks", "Task")
    for a in Activity.objects.filter(type="task"):
        Task.objects.create(
            tenant_id=a.tenant_id, created_by_id=a.created_by_id, created_at=a.created_at, is_deleted=a.is_deleted, deleted_at=a.deleted_at,
            title=(a.notes or "Follow up").strip()[:200], lead_id=a.lead_id, company_id=a.company_id, assignee_id=a.created_by_id,
            due_date=a.due_at.date() if a.due_at else None, status="done" if a.done_at else "todo", done_at=a.done_at,
        )
    Activity.objects.filter(type="task").delete()


class Migration(migrations.Migration):
    dependencies = [("tasks", "0001_initial"), ("crm", "0001_initial")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
