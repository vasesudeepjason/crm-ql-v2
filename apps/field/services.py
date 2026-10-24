"""Field-sales rules shared by views, commands and the approval hook."""
from __future__ import annotations

import zoneinfo
from datetime import datetime, time

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.approvals.models import Approval
from apps.crm.models import Activity, Lead

from .models import AUTO_CLOSE_HOUR, FieldSession, Visit


def active_session(user) -> FieldSession | None:
    return FieldSession.objects.filter(executive=user, ended_at__isnull=True).order_by("-started_at").first()


def close_stale_sessions(tenant_tz: str = "Asia/Kolkata", now=None) -> int:
    """Sessions still open after AUTO_CLOSE_HOUR local time, or from a previous day, close as 'auto'."""
    now = now or timezone.now()
    tz = zoneinfo.ZoneInfo(tenant_tz)
    local = now.astimezone(tz)
    cutoff_today = datetime.combine(local.date(), time(AUTO_CLOSE_HOUR), tz)
    n = 0
    for s in FieldSession.objects.filter(ended_at__isnull=True):
        started_local = s.started_at.astimezone(tz)
        if started_local.date() < local.date() or (local >= cutoff_today and started_local < cutoff_today):
            s.end(FieldSession.EndSource.AUTO)
            n += 1
    return n


def submit_visit(visit: Visit) -> Approval:
    """Every visit goes through the shared approval record (section 5)."""
    summary = {
        "outcome": visit.get_outcome_display(),
        "location": visit.location_label,
        "accuracy": visit.accuracy,
        "mock": visit.mock_location,
        "low_accuracy": visit.low_accuracy,
        "duration": f"{visit.duration_minutes} min" if visit.duration_minutes else "",
        "note": visit.notes,
        "has_photo": bool(visit.photo),
        "contact": visit.contact_name,
    }
    ap = Approval(kind=Approval.Kind.VISIT, content_type=ContentType.objects.get_for_model(Visit), object_id=visit.id, submitter=visit.executive,
                  title=visit.title, company_name=visit.company.name if visit.company else "", value_paise=visit.lead.est_value_paise if visit.lead else None, summary=summary)
    ap.created_by = visit.executive
    ap.save()
    return ap


def on_visit_decided(visit: Visit, approved: bool, by):
    """Approved visits become official: an activity on the lead/company, and the follow-up date moves."""
    if not approved:
        return
    lead = visit.lead
    if lead is None and visit.company_id:
        lead = Lead.objects.filter(company=visit.company, owner=visit.executive).exclude(stage__in=["won", "lost"]).order_by("-updated_at").first()
    a = Activity(lead=lead, company=visit.company, type=Activity.Type.MEETING, notes=f"Field visit · {visit.get_outcome_display()}" + (f"\n{visit.notes}" if visit.notes else ""))
    a.created_by = visit.executive
    a.save()
    if lead is not None:
        if visit.next_follow_up:
            lead.next_follow_up = visit.next_follow_up
            lead.save(update_fields=["next_follow_up", "updated_at"])
        if visit.outcome == Visit.Outcome.INTERESTED and lead.stage in (Lead.Stage.NEW, Lead.Stage.ATTEMPTED, Lead.Stage.CONTACTED, Lead.Stage.FOLLOW_UP):
            lead.move_to(Lead.Stage.INTERESTED, by, "field visit")
        elif visit.outcome == Visit.Outcome.FOLLOW_UP and lead.stage in (Lead.Stage.NEW, Lead.Stage.ATTEMPTED, Lead.Stage.CONTACTED):
            lead.move_to(Lead.Stage.FOLLOW_UP, by, "field visit")
        elif visit.outcome == Visit.Outcome.NOT_INTERESTED and lead.stage not in (Lead.Stage.WON, Lead.Stage.LOST):
            lead.lost_reason = "Not interested (field visit)"
            lead.move_to(Lead.Stage.LOST, by, "field visit")
