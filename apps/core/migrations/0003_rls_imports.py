"""Re-apply row-level security so the new import tables are covered (no-op on SQLite)."""
from django.db import migrations

from apps.core.rls import apply_rls, drop_rls


class Migration(migrations.Migration):
    dependencies = [("core", "0002_row_level_security"), ("imports", "0001_initial"), ("accounts", "0002_otp_invites_totp")]
    operations = [migrations.RunPython(apply_rls, drop_rls)]
