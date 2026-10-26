"""
PostgreSQL row-level security. The database, not application code alone, stops one
customer from reading another's rows (section 7).

Every table that has a `tenant_id` column gets:
  ENABLE + FORCE ROW LEVEL SECURITY
  POLICY tenant_isolation: rows visible / writable only when
      tenant_id = current_setting('app.tenant_id')     (set by TenantMiddleware)
      or current_setting('app.bypass_rls') = 'on'      (platform admin, migrations, seeds)

Tables are discovered from the models, so a new TenantModel is covered by running
`manage.py makemigrations` + the `apply_rls` operation again (see core migration 0002).

Three tables are deliberately exempt (`rls_exempt = True` on the model): Membership,
Invitation and SupportSession. They are how a session *finds* its tenant — the
middleware reads the user's membership before any tenant is set, an invite is accepted
by token before the user belongs anywhere, and support sessions are opened from the
platform side — so a tenant_id policy on them would be circular. Application code
always filters them by tenant explicitly, and the cross-tenant tests cover that.
"""
from django.apps import apps
from django.db import connection


def _is_tenant_model(model) -> bool:
    return not model._meta.abstract and model._meta.managed and any(f.column == "tenant_id" for f in model._meta.local_fields)


def exempt_tables() -> list[str]:
    """Identity tables that carry tenant_id but must stay readable before a tenant is chosen."""
    return [m._meta.db_table for m in apps.get_models() if _is_tenant_model(m) and getattr(m, "rls_exempt", False)]


def tenant_tables() -> list[str]:
    out = []
    for model in apps.get_models():
        if getattr(model, "rls_exempt", False):
            continue
        if model._meta.abstract or not model._meta.managed:
            continue
        if any(f.column == "tenant_id" for f in model._meta.local_fields):
            out.append(model._meta.db_table)
    return sorted(set(out))


POLICY = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON {t};
CREATE POLICY tenant_isolation ON {t}
  USING (
    NULLIF(current_setting('app.bypass_rls', true), '') = 'on'
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint
  )
  WITH CHECK (
    NULLIF(current_setting('app.bypass_rls', true), '') = 'on'
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint
  );
"""


def apply_rls(apps_registry=None, schema_editor=None):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        for t in tenant_tables():
            cur.execute(POLICY.format(t=t))
        for t in exempt_tables():  # a model moved to the exempt list loses its policy
            cur.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}; ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;")


def drop_rls(apps_registry=None, schema_editor=None):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        for t in tenant_tables():
            cur.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}; ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;")
