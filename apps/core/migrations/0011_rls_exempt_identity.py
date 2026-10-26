"""Membership, Invitation and SupportSession are identity tables: remove their policies (no-op on SQLite)."""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [("core", "0010_rls_hr")]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
