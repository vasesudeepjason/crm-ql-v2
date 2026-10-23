"""
CRM core (section 8). The company is the hub: leads, contracts, invoices and visits
all hang off it. Status changes are written as history rows, never overwritten.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel
from apps.core.phones import normalize_phone


class Company(TenantModel):
    name = models.CharField(max_length=160)
    gstin = models.CharField(max_length=15, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    phone_normalized = models.CharField(max_length=20, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source = models.CharField(max_length=60, blank=True)
    tags = models.JSONField(default=list, blank=True)
    custom = models.JSONField(default=dict, blank=True)
    # Imported rows keep their origin so a re-import updates instead of duplicating.
    source_system = models.CharField(max_length=40, blank=True)
    external_id = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["tenant", "name"]), models.Index(fields=["tenant", "source_system", "external_id"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.phone_normalized = normalize_phone(self.phone)
        super().save(*args, **kwargs)


class Contact(TenantModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=120)
    designation = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    phone_normalized = models.CharField(max_length=20, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    is_primary = models.BooleanField(default=False)
    do_not_contact = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.phone_normalized = normalize_phone(self.phone)
        super().save(*args, **kwargs)


class Lead(TenantModel):
    class Stage(models.TextChoices):
        NEW = "new", "New"
        ATTEMPTED = "attempted", "Attempted"
        CONTACTED = "contacted", "Contacted"
        FOLLOW_UP = "follow_up", "Follow up"
        INTERESTED = "interested", "Interested"
        QUALIFIED = "qualified", "Qualified"
        PROPOSAL = "proposal", "Proposal sent"
        NEGOTIATION = "negotiation", "Negotiation"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        ACTIVE = "active", "Active"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads")
    name = models.CharField(max_length=160, help_text="Person or company the lead is about")
    phone = models.CharField(max_length=30, blank=True)
    phone_normalized = models.CharField(max_length=20, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.NEW, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source = models.CharField(max_length=60, blank=True)
    consent_note = models.CharField(max_length=255, blank=True, help_text="How and why this person's data was captured (DPDP)")
    do_not_contact = models.BooleanField(default=False)
    est_value_paise = models.BigIntegerField(null=True, blank=True)
    next_follow_up = models.DateField(null=True, blank=True, db_index=True)
    won_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)
    tags = models.JSONField(default=list, blank=True)
    custom = models.JSONField(default=dict, blank=True)
    source_system = models.CharField(max_length=40, blank=True)
    external_id = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "stage"]), models.Index(fields=["tenant", "owner", "next_follow_up"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.phone_normalized = normalize_phone(self.phone)
        super().save(*args, **kwargs)

    def move_to(self, stage: str, by, note: str = ""):
        """Every stage change is written to history (section 5)."""
        if stage == self.Stage.WON and self.company_id is None:
            raise ValueError("A lead can move to Won only with a company attached")
        old = self.stage
        self.stage = stage
        if stage == self.Stage.WON:
            self.won_at = timezone.now()
        self.save()
        LeadStageHistory.objects.create(lead=self, from_stage=old, to_stage=stage, changed_by=by, note=note)

    @property
    def is_open(self):
        return self.stage not in (self.Stage.WON, self.Stage.LOST)


class LeadStageHistory(TenantModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="history")
    from_stage = models.CharField(max_length=20, blank=True)
    to_stage = models.CharField(max_length=20)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Activity(TenantModel):
    """Calls, notes and tasks against a lead or company. Call logs are the telecaller's unit of work."""

    class Type(models.TextChoices):
        CALL = "call", "Call"
        NOTE = "note", "Note"
        TASK = "task", "Task"
        MEETING = "meeting", "Meeting"

    class Outcome(models.TextChoices):
        CONNECTED = "connected", "Connected"
        NO_ANSWER = "no_answer", "No answer"
        BUSY = "busy", "Busy"
        WRONG_NUMBER = "wrong_number", "Wrong number"
        CALLBACK = "callback", "Call back later"
        NOT_INTERESTED = "not_interested", "Not interested"
        INTERESTED = "interested", "Interested"

    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    type = models.CharField(max_length=10, choices=Type.choices, default=Type.NOTE)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "activities"
        indexes = [models.Index(fields=["tenant", "type", "created_at"])]
