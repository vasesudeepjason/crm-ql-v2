"""A stand-in membership for platform staff inside a support session: Admin access, fully audited."""
from apps.accounts.roles import ROLES


class SupportMembership:
    role = "admin"
    role_name = "QuantumLoop support"
    is_support = True
    manager = None
    job_title = ""

    def __init__(self, session):
        self.session = session
        self.user = session.staff
        self.user_id = session.staff_id
        self.tenant = session.tenant
        self.tenant_id = session.tenant_id
        self.id = None

    def permissions(self):
        return frozenset(ROLES["admin"]["perms"].keys())

    def has_perm(self, perm):
        return perm in ROLES["admin"]["perms"]

    def scope_for(self, perm):
        return ROLES["admin"]["perms"].get(perm)

    def team_user_ids(self):
        return [self.user_id]
