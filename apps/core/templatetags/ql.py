from django import template
from django.utils import timezone

from apps.core import money

register = template.Library()


@register.filter
def rupees(paise):
    return money.rupees(paise)


@register.filter
def short_rupees(paise):
    return money.short_rupees(paise)


@register.filter
def initials(name: str):
    parts = [p for p in (name or "").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


@register.filter
def can(perms_set, perm):
    return perm in perms_set


@register.filter
def get_item(d, key):
    return d.get(key) if isinstance(d, dict) else None


@register.filter
def waiting(dt):
    """'3 hrs' / '2 days' since a timestamp."""
    if not dt:
        return ""
    delta = timezone.now() - dt
    h = int(delta.total_seconds() // 3600)
    if h < 1:
        return f"{int(delta.total_seconds() // 60)} min"
    if h < 24:
        return f"{h} hr{'s' if h != 1 else ''}"
    d = h // 24
    return f"{d} day{'s' if d != 1 else ''}"


@register.simple_tag(takes_context=True)
def active(context, *names):
    rm = context["request"].resolver_match
    current = f"{rm.namespace}:{rm.url_name}" if rm else ""
    return "on" if current in names or (rm and rm.namespace in names) else ""
