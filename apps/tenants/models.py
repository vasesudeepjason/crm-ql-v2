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
