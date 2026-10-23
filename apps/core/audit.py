from __future__ import annotations

from django.forms.models import model_to_dict

from .models import AuditLog


def snapshot(obj, fields=None) -> dict:
    data = model_to_dict(obj, fields=fields)
    return {k: (str(v) if not isinstance(v, (int, float, bool, type(None), str, list, dict)) else v) for k, v in data.items()}


def audit(request, action: str, obj=None, *, before=None, after=None, note: str = ""):
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
