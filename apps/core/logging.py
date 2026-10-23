import logging

from .tenancy import get_current_tenant_id


class TenantFilter(logging.Filter):
    """Every structured log line carries the tenant id (section 7, observability)."""

    def filter(self, record):
        record.tenant = get_current_tenant_id() or "-"
        return True
