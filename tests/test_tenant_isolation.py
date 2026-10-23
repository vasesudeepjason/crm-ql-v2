"""Every endpoint has a test proving a user from tenant A cannot read tenant B (section 6)."""
import pytest
from django.db import connection
from django.urls import reverse

from apps.core.rls import tenant_tables
from apps.core.tenancy import rls_bypass, tenant_context
from apps.crm.models import Company, Lead

ENDPOINTS = ["dashboard:home", "crm:leads", "crm:companies", "approvals:queue"]


@pytest.mark.parametrize("name", ENDPOINTS)
def test_tenant_b_data_never_renders_for_tenant_a(two_tenants, login, name):
    client = login(two_tenants["owner_a"])
    r = client.get(reverse(name))
    assert r.status_code == 200
    body = r.content.decode()
    assert "Lead B" not in body and "B Customer" not in body
    assert "A Co" in body


def test_orm_manager_scopes_to_current_tenant(two_tenants):
    with tenant_context(two_tenants["a"].id):
        assert list(Lead.objects.values_list("name", flat=True)) == ["Lead A"]
    with tenant_context(two_tenants["b"].id):
        assert list(Lead.objects.values_list("name", flat=True)) == ["Lead B"]
    assert Lead.objects.count() == 0  # no tenant context → nothing, never everything


def test_detail_of_other_tenant_is_404(two_tenants, login):
    client = login(two_tenants["owner_b"])
    r = client.get(reverse("approvals:detail", args=[two_tenants["appr"].id]))
    assert r.status_code == 404


def test_save_outside_tenant_context_is_refused(db):
    with pytest.raises(ValueError):
        Company.objects.create(name="Orphan")


def test_every_tenant_model_is_covered_by_rls_list():
    tables = tenant_tables()
    for expected in ("crm_company", "crm_lead", "crm_activity", "approvals_approval", "core_auditlog"):
        assert expected in tables


@pytest.mark.skipif(connection.vendor != "postgresql", reason="row-level security exists only on PostgreSQL")
def test_postgres_rls_blocks_raw_sql_across_tenants(two_tenants):
    """Even raw SQL, bypassing the ORM manager, cannot see the other tenant's rows."""
    with tenant_context(two_tenants["a"].id):
        with connection.cursor() as cur:
            cur.execute("SELECT name FROM crm_lead")
            assert [r[0] for r in cur.fetchall()] == ["Lead A"]
            cur.execute("UPDATE crm_lead SET name='hacked' WHERE name='Lead B'")
            assert cur.rowcount == 0
    with rls_bypass():
        assert Lead.all_objects.count() == 2
