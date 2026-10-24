"""
Field sales (foundation document, sections 4, 5 and 9).

- A FieldSession is one working day on the road. Location is recorded only while a
  session is active, the app says so, and nothing is tracked outside a session.
- A Visit is captured inside a session with a photo, GPS position and accuracy, an
  outcome and notes. It goes through the shared Approval flow; only approved visits
  count in reports and metrics. The outcome sets the lead's next follow-up.
- Sessions close automatically at day end, with the end source recorded.
"""
from __future__ import annotations

import io
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel

AUTO_CLOSE_HOUR = 21  # local time; a session still open after this is closed as "auto"
LOW_ACCURACY_M = 250


def visit_photo_path(instance, filename):
    ext = (filename.rsplit(".", 1)[-1] or "jpg").lower()
    return f"visits/{instance.tenant_id}/{timezone.now():%Y/%m}/{instance.executive_id}-{timezone.now():%d%H%M%S}.{ext if ext in ('jpg', 'jpeg', 'png', 'webp') else 'jpg'}"


class FieldSession(TenantModel):
    class EndSource(models.TextChoices):
        MANUAL = "manual", "Ended by executive"
        AUTO = "auto", "Closed automatically at day end"
        MANAGER = "manager", "Ended by manager"

    executive = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="field_sessions")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    start_lat = models.FloatField(null=True, blank=True)
    start_lng = models.FloatField(null=True, blank=True)
    start_accuracy = models.FloatField(null=True, blank=True, help_text="metres")
    last_lat = models.FloatField(null=True, blank=True)
    last_lng = models.FloatField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_lat = models.FloatField(null=True, blank=True)
    end_lng = models.FloatField(null=True, blank=True)
    end_source = models.CharField(max_length=10, choices=EndSource.choices, blank=True)
    device = models.CharField(max_length=160, blank=True, help_text="user agent, for support")

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["tenant", "executive", "started_at"])]

    def __str__(self):
        return f"{self.executive} · {self.started_at:%d %b %H:%M}"

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    @property
    def duration(self) -> timedelta:
        return (self.ended_at or timezone.now()) - self.started_at

    def end(self, source: str, lat=None, lng=None, by=None):
        if self.ended_at is not None:
            return
        self.ended_at = timezone.now()
        self.end_source = source
        self.end_lat, self.end_lng = lat, lng
        self.save(update_fields=["ended_at", "end_source", "end_lat", "end_lng", "updated_at"])

    def ping(self, lat, lng):
        self.last_lat, self.last_lng, self.last_seen_at = lat, lng, timezone.now()
        self.save(update_fields=["last_lat", "last_lng", "last_seen_at", "updated_at"])


class Visit(TenantModel):
    class Outcome(models.TextChoices):
        INTERESTED = "interested", "Interested"
        FOLLOW_UP = "follow_up", "Follow-up needed"
        ORDER = "order", "Order / renewal agreed"
        PAYMENT = "payment", "Payment collected"
        NOT_INTERESTED = "not_interested", "Not interested"
        NO_SHOW = "no_show", "Contact unavailable"
        SERVICE = "service", "Service / support visit"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        ACTIVE = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    session = models.ForeignKey(FieldSession, on_delete=models.PROTECT, related_name="visits")
    executive = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="visits")
    company = models.ForeignKey("crm.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="visits")
    lead = models.ForeignKey("crm.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="visits")
    contact_name = models.CharField(max_length=120, blank=True)
    photo = models.ImageField(upload_to=visit_photo_path, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    accuracy = models.FloatField(null=True, blank=True, help_text="metres")
    mock_location = models.BooleanField(default=False, help_text="client reported a mock-location provider")
    captured_at = models.DateTimeField(default=timezone.now, db_index=True)
    duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    notes = models.TextField(blank=True)
    next_follow_up = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    offline_id = models.CharField(max_length=64, blank=True, db_index=True, help_text="client id so an offline retry never duplicates")

    class Meta:
        ordering = ["-captured_at"]
        indexes = [models.Index(fields=["tenant", "executive", "captured_at"]), models.Index(fields=["tenant", "status"])]

    def __str__(self):
        return f"{self.company or self.lead or self.contact_name} · {self.get_outcome_display()}"

    @property
    def title(self) -> str:
        return (self.company.name if self.company else None) or (self.lead.name if self.lead else None) or self.contact_name or "Visit"

    @property
    def low_accuracy(self) -> bool:
        return self.accuracy is not None and self.accuracy > LOW_ACCURACY_M

    @property
    def maps_url(self) -> str:
        return f"https://maps.google.com/?q={self.lat},{self.lng}" if self.lat is not None and self.lng is not None else ""

    @property
    def location_label(self) -> str:
        if self.lat is None:
            return "No location"
        acc = f" · ±{int(self.accuracy)} m" if self.accuracy is not None else ""
        return f"{self.lat:.5f}, {self.lng:.5f}{acc}"

    def save(self, *args, **kwargs):
        if self.photo and hasattr(self.photo, "file") and not getattr(self.photo, "_committed", True):
            self.photo = _shrink(self.photo)
        super().save(*args, **kwargs)


def _shrink(f, max_side=1600, quality=78):
    """Server-side safety net: the device compresses first, but never trust it."""
    try:
        from PIL import Image, ImageOps
        from django.core.files.base import ContentFile

        img = Image.open(f)
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_side, max_side))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        name = (f.name.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "photo") + ".jpg"
        return ContentFile(buf.getvalue(), name=name)
    except Exception:
        return f
