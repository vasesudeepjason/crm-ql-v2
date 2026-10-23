"""
Permission checks. A permission is a small named action such as `leads.approve`;
a role is a bundle of them, each with a data scope (own / team / all).
"""
from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied


def has_perm(request, perm: str) -> bool:
    m = getattr(request, "membership", None)
    return bool(m and m.has_perm(perm))


def scope_for(request, perm: str) -> str | None:
    m = getattr(request, "membership", None)
    return m.scope_for(perm) if m else None


def require_perm(perm: str):
    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not has_perm(request, perm):
                raise PermissionDenied(f"Missing permission {perm}")
            return view(request, *args, **kwargs)

        return wrapped

    return deco
