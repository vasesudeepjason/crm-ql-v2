"""
One generic approval record serves both field visits and telecaller leads, so the
approval logic is built once (section 5). Rules:

- A submitter can edit a record only while it is pending or after a rejection.
- A manager cannot approve their own submission. An Admin can.
- A rejection needs a reason, and the submitter is notified.
- A pending item older than two working days is flagged to the manager.
- Approved records are locked.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel

STALE_AFTER_WORKING_DAYS = 2


class ApprovalError(Exception):
    pass


class Approval(TenantModel):
    class Kind(models.TextChoices):
        LEAD = "lead", "Telecaller lead"
        VISIT = "visit", "Field visit"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INFO = "info", "Info requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_approvals")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_approvals")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reason = models.CharField(max_length=255, blank=True)
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    # Denormalised for list screens, so the queue never joins every record type.
    title = models.CharField(max_length=160)
    company_name = models.CharField(max_length=160, blank=True)
    value_paise = models.BigIntegerField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["submitted_at"]
        indexes = [models.Index(fields=["tenant", "status", "submitted_at"]), models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.title} ({self.status})"

    # ---- rules ----
    @property
    def is_open(self):
        return self.status in (self.Status.PENDING, self.Status.INFO)

    @property
    def working_days_waiting(self) -> int:
        d, n = self.submitted_at.date(), 0
        today = timezone.now().date()
        while d < today:
            d = d + timezone.timedelta(days=1)
            if d.weekday() < 6:  # Mon–Sat count as working days in Indian SMBs
                n += 1
        return n

    @property
    def is_stale(self):
        return self.is_open and self.working_days_waiting >= STALE_AFTER_WORKING_DAYS

    def can_decide(self, membership) -> tuple[bool, str]:
        perm = "leads.approve" if self.kind == self.Kind.LEAD else "visits.approve"
        if not membership.has_perm(perm):
            return False, "You cannot approve this kind of record."
        if not self.is_open:
            return False, "This request has already been decided."
        if self.submitter_id == membership.user_id and membership.role not in ("owner", "admin"):
            return False, "You cannot approve your own submission."
        if membership.scope_for(perm) == "team" and self.submitter_id not in membership.team_user_ids():
            return False, "This submission is outside your team."
        return True, ""

    def approve(self, membership):
        ok, why = self.can_decide(membership)
        if not ok:
            raise ApprovalError(why)
        self._decide(membership, self.Status.APPROVED, "")
        self._apply_to_record(approved=True, by=membership.user)

    def reject(self, membership, reason: str):
        ok, why = self.can_decide(membership)
        if not ok:
            raise ApprovalError(why)
        if not (reason or "").strip():
            raise ApprovalError("A rejection needs a reason.")
        self._decide(membership, self.Status.REJECTED, reason.strip())
        self._apply_to_record(approved=False, by=membership.user)

    def request_info(self, membership, note: str):
        ok, why = self.can_decide(membership)
        if not ok:
            raise ApprovalError(why)
        self.status = self.Status.INFO
        self.reason = (note or "").strip()
        self.reviewer = membership.user
        self.save()
        from apps.notifications.services import approval_decided

        approval_decided(self)

    def _decide(self, membership, status, reason):
        self.status = status
        self.reason = reason
        self.reviewer = membership.user
        self.decided_at = timezone.now()
        self.save()
        from apps.notifications.services import approval_decided

        approval_decided(self)

    def _apply_to_record(self, approved: bool, by=None):
        rec = self.record
        if rec is None:
            return
        if hasattr(rec, "status") and hasattr(rec, "Status"):
            rec.status = rec.Status.ACTIVE if approved else rec.Status.REJECTED
            rec.save(update_fields=["status", "updated_at"])
        if self.kind == self.Kind.VISIT:
            from apps.field.services import on_visit_decided

            on_visit_decided(rec, approved, by)
