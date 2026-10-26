"""Scoping, the renewal countdown, reminders (owner 90/60/30/7, client 30/7), renew / lapse, auto-renew roll-over."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.core.money import rupees
from apps.core.permissions import scope_for
from apps.core.tenancy import tenant_context

from .models import Contract, Product

log = logging.getLogger(__name__)

OWNER_DAYS = (90, 60, 30, 7)
CLIENT_DAYS = (30, 7)


def scoped_contracts(request, qs=None):
    qs = Contract.objects.select_related("company", "product", "owner") if qs is None else qs
    scope = scope_for(request, "contracts.view")
    if scope == "all":
        return qs
    if scope == "team":
        return qs.filter(owner_id__in=request.membership.team_user_ids())
    if scope == "own":
        return qs.filter(owner_id=request.user.id)
    return qs.none()


def can_edit(request, c: Contract) -> bool:
    scope = scope_for(request, "contracts.edit")
    if scope == "all":
        return True
    if scope == "team":
        return c.owner_id in request.membership.team_user_ids()
    return scope == "own" and c.owner_id == request.user.id


def can_manage_catalogue(request) -> bool:
    return scope_for(request, "contracts.edit") in ("team", "all")


def renewal_board(qs, today: date | None = None) -> dict:
    """Buckets of active contracts by days to expiry, each with count and annualised revenue at risk."""
    today = today or timezone.localdate()
    active = list(qs.filter(status=Contract.Status.ACTIVE, end_date__lte=today + timedelta(days=90)).order_by("end_date"))
    buckets = {"expired": [], "7": [], "30": [], "60": [], "90": []}
    for c in active:
        buckets[c.bucket].append(c)
    out = {k: {"items": v, "n": len(v), "risk": sum(c.annual_value_paise for c in v)} for k, v in buckets.items()}
    out["total_n"] = len(active)
    out["total_risk"] = sum(c.annual_value_paise for c in active)
    return out


# ---------- decisions ----------

def renew(old: Contract, by, start: date, end: date, amount_paise: int, auto_renew: bool, notes: str = "") -> Contract:
    new = Contract(company=old.company, product=old.product, lead=old.lead, title=old.title, start_date=start, end_date=end, billing_cycle=old.billing_cycle,
                   amount_paise=amount_paise, auto_renew=auto_renew, owner=old.owner, contact=old.contact, contact_email=old.contact_email, notes=notes, renewal_of=old)
    new.created_by = by
    new.save()
    if old.status != Contract.Status.LAPSED:  # a won-back lapsed contract keeps its history as lapsed
        old.status = Contract.Status.RENEWED
    old.decided_at, old.decided_by = timezone.now(), by
    old.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    return new


def lapse(c: Contract, by, reason: str):
    c.status = Contract.Status.LAPSED
    c.lapsed_reason = reason.strip()
    c.decided_at, c.decided_by = timezone.now(), by
    c.save(update_fields=["status", "lapsed_reason", "decided_at", "decided_by", "updated_at"])


# ---------- background ----------

def _owner_notify(c: Contract, days: int):
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    if not c.owner_id:
        return
    when = "expired" if days < 0 else ("expires today" if days == 0 else f"expires in {days} day{'s' if days != 1 else ''}")
    url = reverse("contracts:detail", args=[c.pk])
    notify(c.owner, Notification.Kind.CONTRACT_RENEWAL, f"{c.company.name} · {c.display_title} {when}", f"{rupees(c.annual_value_paise)} a year at risk · ends {c.end_date.strftime('%d %b %Y')}", url,
           email_pref="email_reminders", email_subject=f"[QuantumLoop] Renewal: {c.company.name} {when}",
           email_body=f"{c.company.name}'s {c.display_title} ({c.number}) {when}, on {c.end_date.strftime('%d %b %Y')}.\nValue: {rupees(c.amount_paise)} per {c.get_billing_cycle_display().lower()} · {rupees(c.annual_value_paise)} a year.\n\nMark it renewed or lapsed: {settings.SITE_URL}{url}")


def _client_mail(c: Contract, days: int, tenant_name: str):
    if not c.contact_email:
        return
    when = "today" if days == 0 else f"in {days} days"
    body = (f"Dear {c.contact.name if c.contact else 'Customer'},\n\n"
            f"Your {c.display_title} with {tenant_name} ends on {c.end_date.strftime('%d %B %Y')}, that is {when}. "
            f"To keep the service running without a break, please reply to this email or call your account manager{(' ' + c.owner.name) if c.owner else ''} to renew.\n\n"
            f"Regards,\n{tenant_name}")
    try:
        send_mail(f"{c.display_title} renewal due {when} — {tenant_name}", body, settings.DEFAULT_FROM_EMAIL, [c.contact_email])
    except Exception:
        log.exception("client renewal mail failed")


def send_renewal_reminders(tenant_id: int, today: date | None = None) -> dict:
    """Idempotent per threshold: a reminder is sent once, even if the job missed the exact day."""
    from apps.tenants.models import Tenant

    today = today or timezone.localdate()
    sent = {"owner": 0, "client": 0}
    with tenant_context(tenant_id):
        tenant_name = Tenant.objects.get(pk=tenant_id).name
        for c in Contract.objects.filter(status=Contract.Status.ACTIVE, end_date__lte=today + timedelta(days=90)).select_related("company", "product", "owner", "contact"):
            days = (c.end_date - today).days
            done_o = set(c.reminders.get("owner", []))
            done_c = set(c.reminders.get("client", []))
            changed = False
            due_o = [d for d in OWNER_DAYS if days <= d and d not in done_o]
            if due_o:  # one message for the tightest threshold reached, mark all reached
                _owner_notify(c, days)
                done_o.update(due_o)
                sent["owner"] += 1
                changed = True
            due_c = [d for d in CLIENT_DAYS if 0 <= days <= d and d not in done_c]
            if due_c and c.contact_email:
                _client_mail(c, days, tenant_name)
                done_c.update(due_c)
                sent["client"] += 1
                changed = True
            if changed:
                c.reminders = {"owner": sorted(done_o, reverse=True), "client": sorted(done_c, reverse=True)}
                c.save(update_fields=["reminders", "updated_at"])
    return sent


def roll_auto_renewals(tenant_id: int, today: date | None = None) -> int:
    """Contracts flagged auto-renew roll into a new term of the same length the day after they end."""
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    today = today or timezone.localdate()
    n = 0
    with tenant_context(tenant_id):
        for c in Contract.objects.filter(status=Contract.Status.ACTIVE, auto_renew=True, end_date__lt=today).select_related("company", "product", "owner"):
            start, end = c.next_term()
            new = renew(c, None, start, end, c.amount_paise, True, notes="Auto-renewed")
            n += 1
            if c.owner_id:
                notify(c.owner, Notification.Kind.CONTRACT_RENEWAL, f"Auto-renewed: {c.company.name} · {c.display_title}", f"New term {start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')} · {new.number}", reverse("contracts:detail", args=[new.pk]), email_pref="email_reminders")
    return n


def at_risk_summary(qs, today: date | None = None) -> dict:
    today = today or timezone.localdate()
    q = qs.filter(status=Contract.Status.ACTIVE, end_date__lte=today + timedelta(days=30))
    return {"n": q.count(), "risk": sum(c.annual_value_paise for c in q)}


def active_book(qs) -> int:
    """Annualised value of every active contract (the recurring book)."""
    return sum(c.annual_value_paise for c in qs.filter(status=Contract.Status.ACTIVE).select_related("product"))
