"""
Import wizard (foundation document, section 11). One ImportJob per uploaded file;
one ImportRow per data row, keeping the raw values so nothing is lost silently.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel

ROLLBACK_WINDOW_DAYS = 14


def upload_to(instance, filename):
    return f"imports/{instance.tenant_id}/{timezone.now():%Y%m%d%H%M%S}-{filename}"


class SavedMapping(TenantModel):
    """A column → field mapping remembered for the next file from the same source."""

    entity = models.CharField(max_length=20)
    source = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    columns_key = models.CharField(max_length=64, db_index=True, help_text="hash of the file's header row")
    mapping = models.JSONField(default=dict)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.entity} · {self.source})"


class ImportJob(TenantModel):
    class Entity(models.TextChoices):
        COMPANIES = "companies", "Companies"
        LEADS = "leads", "Leads"

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        MAPPED = "mapped", "Mapped"
        VALIDATED = "validated", "Validated (dry run)"
        COMMITTED = "committed", "Imported"
        ROLLED_BACK = "rolled_back", "Rolled back"
        FAILED = "failed", "Failed"

    entity = models.CharField(max_length=20, choices=Entity.choices)
    source = models.CharField(max_length=40, default="generic")
    file = models.FileField(upload_to=upload_to)
    filename = models.CharField(max_length=255)
    columns = models.JSONField(default=list, help_text="header row, in file order")
    row_count = models.PositiveIntegerField(default=0)
    mapping = models.JSONField(default=dict, help_text='{"column": "field", ..., "stage_map": {...}}')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADED, db_index=True)
    counts = models.JSONField(default=dict, help_text="new / update / skip / error after validation")
    validated_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_entity_display()} import · {self.filename} ({self.status})"

    @property
    def can_rollback(self) -> bool:
        return self.status == self.Status.COMMITTED and self.committed_at is not None and timezone.now() - self.committed_at <= timedelta(days=ROLLBACK_WINDOW_DAYS)

    @property
    def rollback_deadline(self):
        return self.committed_at + timedelta(days=ROLLBACK_WINDOW_DAYS) if self.committed_at else None

    @property
    def field_mapping(self) -> dict[str, str]:
        return {c: f for c, f in self.mapping.items() if c != "stage_map" and f}

    @property
    def stage_map(self) -> dict[str, str]:
        return self.mapping.get("stage_map", {})


class ImportRow(TenantModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        UPDATE = "update", "Update"
        SKIP = "skip", "Skipped"
        ERROR = "error", "Needs fixing"

    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="rows")
    row_no = models.PositiveIntegerField()
    raw = models.JSONField(default=dict)
    mapped = models.JSONField(default=dict)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.NEW, db_index=True)
    messages = models.JSONField(default=list, help_text="validation errors / skip reasons / duplicate suggestions")
    duplicate_of = models.CharField(max_length=160, blank=True, help_text="label of the existing record this looks like")
    # what the commit did, so a rollback can undo it
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_ids = models.JSONField(default=dict, blank=True, help_text='e.g. {"company": 12, "contact": 30}')
    before = models.JSONField(null=True, blank=True, help_text="snapshot of an updated record, for rollback")

    class Meta:
        ordering = ["row_no"]
        indexes = [models.Index(fields=["job", "status"])]
