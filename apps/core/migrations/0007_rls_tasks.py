"""Re-apply row-level security so the tasks tables are covered (no-op on SQLite)."""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [("core", "0006_rls_notifications"), ("tasks", "0002_activity_tasks")]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
