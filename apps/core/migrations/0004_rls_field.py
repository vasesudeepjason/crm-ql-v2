"""Re-apply row-level security so the field tables are covered (no-op on SQLite)."""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [("core", "0003_rls_imports"), ("field", "0001_initial")]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
