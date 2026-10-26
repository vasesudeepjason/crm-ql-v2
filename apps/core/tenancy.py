"""
Tenant context.

The current tenant is resolved from the signed-in user's membership and held in a
context variable for the duration of the request. Two layers use it:

1. `TenantManager` filters every ORM query on `tenant_id` (application layer).
2. On PostgreSQL the same id is set as `app.tenant_id` on the connection, and the
   row-level-security policies (see core migration 0002) refuse rows from any other
   tenant (database layer). The database rule is the guarantee; the manager is the
   convenience.

Platform staff open a tenant only through a logged support session (see
`support_session`), never silently.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

from django.db import connection
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

_current_tenant_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("tenant_id", default=None)
_bypass: contextvars.ContextVar[bool] = contextvars.ContextVar("rls_bypass", default=False)


def get_current_tenant_id() -> int | None:
    return _current_tenant_id.get()


def _apply_to_connection():
    """Push the context onto the DB session so RLS policies see it (Postgres only)."""
    if connection.vendor != "postgresql":
        return
    tid = _current_tenant_id.get()
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", [str(tid) if tid else ""])
        cur.execute("SELECT set_config('app.bypass_rls', %s, false)", ["on" if _bypass.get() else ""])


@contextmanager
def tenant_context(tenant_id: int | None):
    """Run a block scoped to one tenant (jobs, seeds, tests). A tenant context never bypasses RLS."""
    token = _current_tenant_id.set(tenant_id)
    btoken = _bypass.set(False)
    try:
        _apply_to_connection()
        yield
    finally:
        _current_tenant_id.reset(token)
        _bypass.reset(btoken)
        _apply_to_connection()


@contextmanager
def rls_bypass():
    """Cross-tenant access for migrations, platform admin and tests. Always explicit."""
    token = _bypass.set(True)
    try:
        _apply_to_connection()
        yield
    finally:
        _bypass.reset(token)
        _apply_to_connection()


class TenantMiddleware:
    """Attach `request.tenant` and `request.membership`, and scope the DB connection."""

    EXEMPT_PREFIXES = ("/accounts/", "/platform/", "/static/", "/media/", "/healthz", "/offline/", "/sw.js")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.membership = None
        request.support_session = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if user.is_staff:
                self._attach_support_session(request, user)
            if request.tenant is None:
                membership = user.active_membership()
                if membership is not None:
                    request.tenant = membership.tenant
                    request.membership = membership
        exempt = request.path.startswith(self.EXEMPT_PREFIXES)
        if request.tenant is None and user is not None and user.is_authenticated and not exempt:
            if user.is_staff:
                return redirect("/platform/")
            return HttpResponseForbidden("Your account is not a member of any workspace. Ask your admin for an invite.")

        from django.conf import settings as dj_settings

        timezone.activate((request.tenant.timezone if request.tenant else "") or dj_settings.DEFAULT_TENANT_TIMEZONE)
        token = _current_tenant_id.set(request.tenant.id if request.tenant else None)
        btoken = _bypass.set(bool(user and user.is_authenticated and user.is_staff and request.path.startswith("/platform/")))
        try:
            _apply_to_connection()
            return self.get_response(request)
        finally:
            _current_tenant_id.reset(token)
            _bypass.reset(btoken)
            _apply_to_connection()
            timezone.deactivate()

    @staticmethod
    def _attach_support_session(request, user):
        """A staff member with an active support session acts as Admin inside that tenant."""
        sid = request.session.get("support_session_id")
        if not sid:
            return
        from apps.tenants.models import SupportSession
        from apps.tenants.support import SupportMembership

        ss = SupportSession.objects.select_related("tenant", "tenant__plan", "staff").filter(pk=sid, staff=user).first()
        if ss is None:
            request.session.pop("support_session_id", None)
            return
        if not ss.is_active:
            if ss.ended_at is None:
                ss.end(reason="expired")
                from apps.core.models import AuditLog

                AuditLog.objects.create(tenant=ss.tenant, actor=user, action="support.expire", model="SupportSession", object_id=str(ss.pk))
            request.session.pop("support_session_id", None)
            return
        request.tenant = ss.tenant
        request.membership = SupportMembership(ss)
        request.support_session = ss
