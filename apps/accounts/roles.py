"""
Fixed roles for Phase 1 (foundation document, section 3). Custom roles come later as
a paid-plan feature. Each permission carries a data scope: own, team or all.
"""
from __future__ import annotations

OWN, TEAM, ALL = "own", "team", "all"

# Every permission the platform knows. Modules add to this list as they ship.
PERMISSIONS = [
    "leads.view", "leads.create", "leads.edit", "leads.approve", "leads.assign", "leads.export",
    "companies.view", "companies.create", "companies.edit",
    "visits.view", "visits.create", "visits.approve",
    "calls.view", "calls.create",
    "contracts.view", "contracts.create", "contracts.edit",
    "invoices.view", "invoices.create", "invoices.edit",
    "payments.view", "payments.create",
    "reports.view",
    "hr.view", "hr.edit",
    "attendance.view", "attendance.edit",
    "tasks.view", "tasks.create",
    "users.manage", "settings.manage", "billing.manage", "data.import", "data.export",
    "audit.view",
]

_SALES_READ = {"leads.view", "companies.view", "visits.view", "calls.view", "tasks.view", "tasks.create"}
_SALES_WRITE = {"leads.create", "leads.edit", "companies.create", "companies.edit", "calls.create"}
_FINANCE = {"contracts.view", "contracts.create", "contracts.edit", "invoices.view", "invoices.create", "invoices.edit", "payments.view", "payments.create"}
_HR = {"hr.view", "hr.edit", "attendance.view", "attendance.edit"}


def _all(perms):
    return {p: ALL for p in perms}


ROLES: dict[str, dict] = {
    "owner": {"name": "Owner", "perms": _all(PERMISSIONS)},
    "admin": {"name": "Admin", "perms": {p: ALL for p in PERMISSIONS if p != "billing.manage"}},
    "manager": {
        "name": "Manager",
        "perms": {**{p: TEAM for p in _SALES_READ | _SALES_WRITE}, "leads.approve": TEAM, "visits.approve": TEAM, "leads.assign": TEAM, "reports.view": TEAM, "contracts.view": TEAM, "contracts.create": TEAM, "contracts.edit": TEAM, "invoices.view": TEAM, "invoices.create": TEAM, "payments.view": TEAM},
    },
    "sales": {"name": "Sales Executive", "perms": {**{p: OWN for p in _SALES_READ | _SALES_WRITE}, "contracts.view": OWN, "contracts.create": OWN, "contracts.edit": OWN, "invoices.create": OWN, "invoices.view": OWN}},
    "telecaller": {"name": "Telecaller", "perms": {"leads.view": OWN, "leads.create": OWN, "calls.view": OWN, "calls.create": OWN, "tasks.view": OWN, "companies.view": OWN}},
    "field": {"name": "Field Executive", "perms": {"visits.view": OWN, "visits.create": OWN, "leads.view": OWN, "companies.view": ALL, "companies.create": OWN, "tasks.view": OWN}},
    "accountant": {"name": "Accountant", "perms": {**_all(_FINANCE), "companies.view": ALL, "reports.view": ALL, "data.export": ALL}},
    "hr": {"name": "HR", "perms": _all(_HR)},
    "viewer": {"name": "Viewer", "perms": {p: ALL for p in PERMISSIONS if p.endswith(".view")}},
}

ROLE_CHOICES = [(code, r["name"]) for code, r in ROLES.items()]
