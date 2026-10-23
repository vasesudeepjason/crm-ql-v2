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
"""
from django.apps import apps
from django.db import connection


def tenant_tables() -> list[str]:
    out = []
    for model in apps.get_models():
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


def drop_rls(apps_registry=None, schema_editor=None):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        for t in tenant_tables():
            cur.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}; ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;")
