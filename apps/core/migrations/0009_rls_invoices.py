"""Re-apply row-level security so the invoice tables are covered (no-op on SQLite)."""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [("core", "0008_rls_contracts"), ("invoices", "0001_initial")]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
