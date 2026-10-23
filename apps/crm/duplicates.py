"""Duplicate detection on entry (section 4, CRM). Suggest matches; never merge automatically."""
from __future__ import annotations

import difflib

from django.db.models import Q

from apps.core.phones import normalize_phone

from .models import Lead

FUZZY = 0.9


def find_duplicates(*, phone: str = "", email: str = "", name: str = "", exclude_id=None, limit: int = 5) -> list[dict]:
    norm = normalize_phone(phone)
    email = (email or "").strip().lower()
    name = (name or "").strip()
    out: dict[int, dict] = {}

    def add(lead, why):
        if lead.id in out or lead.id == exclude_id:
            return
        out[lead.id] = {"lead": lead, "why": why}

    if norm:
        for l in Lead.objects.filter(phone_normalized=norm).select_related("company", "owner")[:limit]:
            add(l, "same phone")
    if email:
        for l in Lead.objects.filter(email__iexact=email).select_related("company", "owner")[:limit]:
            add(l, "same email")
    if name and len(name) >= 4 and len(out) < limit:
        cands = {l.name.lower(): l for l in Lead.objects.filter(Q(name__icontains=name[:4]) | Q(company__name__icontains=name[:4])).select_related("company", "owner")[:200]}
        for n in difflib.get_close_matches(name.lower(), cands.keys(), n=limit, cutoff=FUZZY):
            add(cands[n], "similar name")
    return list(out.values())[:limit]
