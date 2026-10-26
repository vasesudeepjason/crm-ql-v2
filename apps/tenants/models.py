from django.conf import settings
from django.db import models
from django.utils.timezone import now as tz_now


class Plan(models.Model):
    """Starter / Growth / Business (section 10). Limits are enforced per tenant from here."""

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=40)
    price_paise_month = models.PositiveIntegerField(help_text="Annual-billing price per month, excl. GST")
    users_included = models.PositiveSmallIntegerField()
    storage_gb = models.PositiveSmallIntegerField()
    modules = models.JSONField(default=list, help_text='e.g. ["crm","telecaller","tasks"]')
    custom_roles = models.BooleanField(default=False)
    api_access = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Tenant(models.Model):
    class Status(models.TextChoices):
        PILOT = "pilot", "Pilot"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        CLOSED = "closed", "Closed"

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    plan = models.ForeignKey(Plan, null=True, on_delete=models.PROTECT, related_name="tenants")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PILOT)
    timezone = models.CharField(max_length=40, default=settings.DEFAULT_TENANT_TIMEZONE)
    gstin = models.CharField(max_length=15, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(default=tz_now)
    pilot_ends_at = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name

    @property
    def user_limit(self):
        return self.plan.users_included if self.plan else 5


class SupportSession(models.Model):
    """
    Platform staff enter a tenant only through a time-limited, logged session that the
    tenant admin can see (section 9). While active, the staff member acts as an Admin
    inside that workspace; every action is audited with the session id.
    """

    rls_exempt = True  # opened from the platform side, before the staff member has a tenant

    DEFAULT_MINUTES = 60

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="support_sessions")
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_sessions")
    reason = models.CharField(max_length=255)
    started_at = models.DateTimeField(default=tz_now)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    end_reason = models.CharField(max_length=20, blank=True)  # left / revoked / expired

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.staff} in {self.tenant} ({self.started_at:%d %b %H:%M})"

    @property
    def is_active(self) -> bool:
        return self.ended_at is None and self.expires_at > tz_now()

    def end(self, by=None, reason="left"):
        if self.ended_at is None:
            self.ended_at = tz_now()
            self.ended_by = by
            self.end_reason = reason
            self.save(update_fields=["ended_at", "ended_by", "end_reason"])
