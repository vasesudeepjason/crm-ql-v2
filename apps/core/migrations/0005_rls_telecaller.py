"""Re-apply row-level security so the telecaller tables are covered (no-op on SQLite)."""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [("core", "0004_rls_field"), ("telecaller", "0001_initial")]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
