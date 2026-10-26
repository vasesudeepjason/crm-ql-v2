"""
Deploy-time system checks (run by `manage.py check` and at startup).

RLS is only a guarantee when the application role cannot bypass it. A PostgreSQL
superuser or a role with BYPASSRLS sees every tenant's rows, so outside DEBUG that is
an error, not a warning.
"""
from django.conf import settings
from django.core.checks import Error, Warning, register
from django.db import connection


@register("quantumloop", deploy=False)
def check_database_role(app_configs, **kwargs):
    out = []
    if connection.vendor != "postgresql":
        if not settings.DEBUG and not getattr(settings, "TESTING", False):
            out.append(Error("Production must run on PostgreSQL; row-level security needs it.", id="quantumloop.E001"))
        return out
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            row = cur.fetchone()
    except Exception as e:  # database down: other checks will say so
        return [Warning(f"Could not inspect the database role: {e}", id="quantumloop.W001")]
    if row and (row[0] or row[1]):
        msg = "The database role is a SUPERUSER or has BYPASSRLS, so row-level security is NOT enforced. Connect as a plain role that owns the schema (see docker/initdb/01-app-role.sql)."
        out.append(Error(msg, id="quantumloop.E002") if not settings.DEBUG else Warning(msg, id="quantumloop.W002"))
    return out


@register("quantumloop", deploy=True)
def check_secrets(app_configs, **kwargs):
    out = []
    if not settings.DEBUG:
        if not getattr(settings, "FIELD_ENCRYPTION_KEY", ""):
            out.append(Error("FIELD_ENCRYPTION_KEY is not set; encrypted fields would use a key derived from SECRET_KEY.", id="quantumloop.E003"))
        if settings.SECRET_KEY.startswith("dev-only"):
            out.append(Error("DJANGO_SECRET_KEY is the development default.", id="quantumloop.E004"))
        if settings.SITE_URL.startswith("http://localhost"):
            out.append(Warning("SITE_URL still points at localhost; links in emails will be wrong.", id="quantumloop.W003"))
    return out
