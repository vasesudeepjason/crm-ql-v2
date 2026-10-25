import pytest
from django.contrib.contenttypes.models import ContentType

from apps.accounts.models import Membership, User
from apps.approvals.models import Approval
from apps.core.tenancy import tenant_context
from apps.crm.models import Company, Lead
from apps.tenants.models import Tenant

PW = "test-password-123"


@pytest.fixture
def two_tenants(db):
    """Tenant A (owner, manager, telecaller under the manager) and tenant B (owner), each with one lead."""
    a = Tenant.objects.create(name="A Co", slug="a")
    b = Tenant.objects.create(name="B Co", slug="b")

    def mk(email, tenant, role, manager=None):
        u = User.objects.create_user(email, PW, name=email.split("@")[0].title())
        m = Membership.objects.create(user=u, tenant=tenant, role=role, manager=manager)
        return u, m

    owner_a, _ = mk("owner@a.test", a, "owner")
    mgr_a, m_mgr = mk("mgr@a.test", a, "manager")
    tc_a, _ = mk("tc@a.test", a, "telecaller", m_mgr)
    other_tc, _ = mk("tc2@a.test", a, "telecaller")  # not in the manager's team
    owner_b, _ = mk("owner@b.test", b, "owner")

    with tenant_context(a.id):
        ca = Company.objects.create(name="A Customer", owner=owner_a)
        la = Lead.objects.create(name="Lead A", company=ca, owner=tc_a)
        ct = ContentType.objects.get_for_model(Lead)
        appr = Approval.objects.create(kind="lead", content_type=ct, object_id=la.id, submitter=tc_a, title="Lead A", company_name="A Customer")
        appr_other = Approval.objects.create(kind="lead", content_type=ct, object_id=la.id, submitter=other_tc, title="Lead A2")
    with tenant_context(b.id):
        cb = Company.objects.create(name="B Customer", owner=owner_b)
        lb = Lead.objects.create(name="Lead B", company=cb, owner=owner_b)

    from django.core import mail

    mail.outbox.clear()  # approval creation above emailed the managers
    return dict(a=a, b=b, owner_a=owner_a, mgr_a=mgr_a, m_mgr=m_mgr, tc_a=tc_a, other_tc=other_tc, owner_b=owner_b, lead_a=la, lead_b=lb, appr=appr, appr_other=appr_other)


@pytest.fixture
def login(client):
    def _login(user):
        assert client.login(username=user.email, password=PW)
        return client

    return _login
