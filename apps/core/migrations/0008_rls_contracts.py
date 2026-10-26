"""Re-apply row-level security so the contracts tables are covered (no-op on SQLite)."""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [("core", "0007_rls_tasks"), ("contracts", "0001_initial"), ("tasks", "0003_task_contract")]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
