from __future__ import annotations

from django.forms.models import model_to_dict

from .models import AuditLog
from .tenancy import get_current_tenant_id, rls_bypass


def snapshot(obj, fields=None) -> dict:
    data = model_to_dict(obj, fields=fields)
    return {k: (str(v) if not isinstance(v, (int, float, bool, type(None), str, list, dict)) else v) for k, v in data.items()}


def audit(request, action: str, obj=None, *, before=None, after=None, note: str = ""):
    ss = getattr(request, "support_session", None)
    if ss is not None:
        note = f"support session #{ss.pk}" + (f" · {note}" if note else "")
    # Platform-level events (sign-ins, support sessions, OTP) happen before a tenant is chosen;
    # their rows have no tenant_id and may only be written with RLS bypassed.
    tenant_id = get_current_tenant_id()
    tenant = getattr(request, "tenant", None) if request is not None else None
    ctx = rls_bypass() if tenant_id is None or (tenant is not None and tenant.id != tenant_id) else _noop()
    with ctx:
        _write(request, action, obj, before, after, note)


class _noop:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _write(request, action, obj, before, after, note):
    AuditLog.objects.create(
        actor=request.user if request is not None and request.user.is_authenticated else None,
        action=action,
        model=type(obj).__name__ if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        before=before,
        after=after,
        note=note,
        ip=_client_ip(request) if request is not None else None,
    )


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
