"""
Accounting and renewals, part 1 (foundation document section 4 and 5): the product and
service catalogue, one contract per purchase, and the renewal countdown. Invoices and
payments hang off contracts in part 2. Money is in paise.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel

GST_RATES = [(Decimal("0"), "0% (exempt)"), (Decimal("5"), "5%"), (Decimal("12"), "12%"), (Decimal("18"), "18%"), (Decimal("28"), "28%")]


class Product(TenantModel):
    class Kind(models.TextChoices):
        SERVICE = "service", "Service (SAC)"
        GOODS = "goods", "Goods (HSN)"

    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.SERVICE)
    hsn_sac = models.CharField("HSN / SAC code", max_length=8, blank=True)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=2, choices=GST_RATES, default=Decimal("18"))
    default_price_paise = models.BigIntegerField(default=0)
    default_cycle = models.CharField(max_length=12, choices=[], default="yearly")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["tenant", "is_active"])]

    def __str__(self):
        return self.name


class Contract(TenantModel):
    class Cycle(models.TextChoices):
        ONE_TIME = "one_time", "One-time"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        HALF_YEARLY = "half_yearly", "Half-yearly"
        YEARLY = "yearly", "Yearly"

    CYCLES_PER_YEAR = {"one_time": 1, "monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1}

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RENEWED = "renewed", "Renewed"
        LAPSED = "lapsed", "Lapsed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=24, blank=True, db_index=True)
    company = models.ForeignKey("crm.Company", on_delete=models.PROTECT, related_name="contracts")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="contracts")
    lead = models.ForeignKey("crm.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="contracts")
    title = models.CharField(max_length=160, blank=True, help_text="Shown instead of the product name when set")
    start_date = models.DateField()
    end_date = models.DateField(db_index=True)
    billing_cycle = models.CharField(max_length=12, choices=Cycle.choices, default=Cycle.YEARLY)
    amount_paise = models.BigIntegerField(help_text="Per billing cycle, before GST")
    auto_renew = models.BooleanField(default=False, help_text="Roll into a new term of the same length on expiry")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="contracts")
    contact = models.ForeignKey("crm.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    contact_email = models.EmailField(blank=True, help_text="Where client reminders go")
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    renewal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="renewals")
    lapsed_reason = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reminders = models.JSONField(default=dict, blank=True, help_text='{"owner": [90, 60], "client": [30]} days already reminded')

    class Meta:
        ordering = ["end_date"]
        indexes = [models.Index(fields=["tenant", "status", "end_date"]), models.Index(fields=["tenant", "owner", "status"])]

    def __str__(self):
        return f"{self.number} · {self.display_title}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self._next_number()
        super().save(*args, **kwargs)

    def _next_number(self) -> str:
        """CT/26-27/0001 — a series per tenant per Indian financial year."""
        from apps.core.tenancy import get_current_tenant_id

        d = self.start_date or timezone.localdate()
        fy = d.year if d.month >= 4 else d.year - 1
        prefix = f"CT/{fy % 100:02d}-{(fy + 1) % 100:02d}/"
        tid = self.tenant_id or get_current_tenant_id()
        n = Contract.all_objects.filter(tenant_id=tid, number__startswith=prefix).count() + 1
        return f"{prefix}{n:04d}"

    # ----- derived -----
    @property
    def display_title(self):
        return self.title or self.product.name

    CYCLE_NOUN = {"one_time": "one-time", "monthly": "month", "quarterly": "quarter", "half_yearly": "half-year", "yearly": "year"}

    @property
    def cycle_noun(self) -> str:
        return self.CYCLE_NOUN.get(self.billing_cycle, self.billing_cycle)

    @property
    def term_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def days_left(self) -> int:
        return (self.end_date - timezone.localdate()).days

    @property
    def is_open(self):
        return self.status == self.Status.ACTIVE

    @property
    def is_expired(self):
        return self.is_open and self.days_left < 0

    @property
    def bucket(self) -> str:
        """expired / 7 / 30 / 60 / 90 / later — only meaningful for active contracts."""
        d = self.days_left
        if d < 0:
            return "expired"
        for b in (7, 30, 60, 90):
            if d <= b:
                return str(b)
        return "later"

    @property
    def annual_value_paise(self) -> int:
        return self.amount_paise * self.CYCLES_PER_YEAR.get(self.billing_cycle, 1)

    @property
    def gst_paise(self) -> int:
        return int(round(self.amount_paise * self.product.gst_rate / 100))

    @property
    def total_paise(self) -> int:
        return self.amount_paise + self.gst_paise

    def next_term(self) -> tuple[date, date]:
        start = self.end_date + timedelta(days=1)
        return start, start + timedelta(days=self.term_days - 1)

    @property
    def renewed_by(self):
        return self.renewals.order_by("-created_at").first()


# Product.default_cycle choices reference Contract.Cycle; wire them after both classes exist.
Product._meta.get_field("default_cycle").choices = Contract.Cycle.choices
