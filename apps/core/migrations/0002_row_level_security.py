"""
Enables PostgreSQL row-level security on every tenant table. No-op on SQLite.
Re-run whenever a new tenant table is added: `manage.py apply_rls`.
"""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("tenants", "0001_initial"),
        ("accounts", "0001_initial"),
        ("crm", "0001_initial"),
        ("approvals", "0001_initial"),
    ]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
