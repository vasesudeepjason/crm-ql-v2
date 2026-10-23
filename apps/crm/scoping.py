"""Apply a permission's data scope (own / team / all) to a queryset."""
from apps.core.permissions import scope_for


def scoped(request, qs, perm: str, owner_field: str = "owner_id"):
    scope = scope_for(request, perm)
    if scope == "all":
        return qs
    if scope == "team":
        return qs.filter(**{f"{owner_field}__in": request.membership.team_user_ids()})
    if scope == "own":
        return qs.filter(**{owner_field: request.user.id})
    return qs.none()
