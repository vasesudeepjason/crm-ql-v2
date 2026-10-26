"""
Invoices and payments (foundation document: GSTIN, place of supply, CGST/SGST/IGST split,
part payments, outstanding and ageing). Money is in paise; tax is computed per line and
rounded per line so the PDF and the screen always agree.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel

from .gst import STATE_CHOICES, state_code_from_gstin, tax_mode


class InvoiceSettings(models.Model):
    """Supplier details printed on every invoice. One per workspace, edited on the Workspace page."""

    tenant = models.OneToOneField("tenants.Tenant", on_delete=models.CASCADE, related_name="invoice_settings")
    legal_name = models.CharField(max_length=160, blank=True, help_text="Name as on the GST certificate; defaults to the workspace name")
    address = models.TextField(blank=True)
    state_code = models.CharField(max_length=2, choices=STATE_CHOICES, blank=True, help_text="Supplier state; drives CGST+SGST vs IGST")
    pan = models.CharField(max_length=10, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    website = models.CharField(max_length=120, blank=True)
    logo = models.ImageField(upload_to="invoice_logos/", blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_no = models.CharField(max_length=30, blank=True)
    bank_ifsc = models.CharField(max_length=11, blank=True)
    upi_id = models.CharField(max_length=80, blank=True)
    series_prefix = models.CharField(max_length=10, default="INV")
    due_days = models.PositiveSmallIntegerField(default=15)
    default_notes = models.CharField(max_length=255, blank=True)
    default_terms = models.TextField(blank=True, default="Payment due within the credit period shown.\nInterest at 18% p.a. on overdue amounts.\nSubject to local jurisdiction.", help_text="One per line")

    def __str__(self):
        return f"invoice settings · {self.tenant}"

    @classmethod
    def for_tenant(cls, tenant) -> "InvoiceSettings":
        obj, created = cls.objects.get_or_create(tenant=tenant)
        if created or not obj.state_code:
            code = state_code_from_gstin(tenant.gstin)
            if code and obj.state_code != code:
                obj.state_code = code
                obj.save(update_fields=["state_code"])
        return obj

    @property
    def terms_list(self):
        return [t.strip() for t in (self.default_terms or "").splitlines() if t.strip()]

    @property
    def is_complete(self):
        return bool(self.address and self.state_code)


class Invoice(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PART = "part_paid", "Partly paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    class Mode(models.TextChoices):
        INTRA = "intra", "CGST + SGST"
        INTER = "inter", "IGST"

    number = models.CharField(max_length=24, blank=True, db_index=True)
    company = models.ForeignKey("crm.Company", on_delete=models.PROTECT, related_name="invoices")
    contract = models.ForeignKey("contracts.Contract", null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    # Buyer snapshot: an invoice must read the same years later even if the company record changes.
    bill_to_name = models.CharField(max_length=160)
    bill_to_address = models.TextField(blank=True)
    bill_to_gstin = models.CharField(max_length=15, blank=True)
    bill_to_state = models.CharField(max_length=2, choices=STATE_CHOICES, blank=True)
    bill_to_email = models.EmailField(blank=True)
    ship_to_name = models.CharField(max_length=160, blank=True)
    ship_to_address = models.TextField(blank=True)
    place_of_supply = models.CharField(max_length=2, choices=STATE_CHOICES, blank=True)
    supplier_state = models.CharField(max_length=2, blank=True)
    tax_mode = models.CharField(max_length=5, choices=Mode.choices, default=Mode.INTRA)
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    subtotal_paise = models.BigIntegerField(default=0)
    cgst_paise = models.BigIntegerField(default=0)
    sgst_paise = models.BigIntegerField(default=0)
    igst_paise = models.BigIntegerField(default=0)
    round_off_paise = models.IntegerField(default=0)
    total_paise = models.BigIntegerField(default=0)
    received_paise = models.BigIntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True)
    terms = models.TextField(blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [models.Index(fields=["tenant", "status", "due_date"]), models.Index(fields=["tenant", "company", "status"]), models.Index(fields=["tenant", "number"])]

    def __str__(self):
        return self.number or f"Draft #{self.pk}"

    # ----- numbering -----
    @staticmethod
    def fy_label(d) -> str:
        fy = d.year if d.month >= 4 else d.year - 1
        return f"{fy % 100:02d}-{(fy + 1) % 100:02d}"

    def assign_number(self, prefix: str):
        """Called once, on issue. Series restarts every financial year (April to March)."""
        if self.number:
            return
        series = f"{prefix}/{self.fy_label(self.issue_date)}/"
        n = Invoice.all_objects.filter(tenant_id=self.tenant_id, number__startswith=series).count() + 1
        self.number = f"{series}{n:04d}"

    # ----- money -----
    def recompute(self, save=True):
        lines = list(self.lines.all())
        self.subtotal_paise = sum(l.amount_paise for l in lines)
        tax = sum(l.tax_paise for l in lines)
        self.tax_mode = tax_mode(self.supplier_state, self.place_of_supply)
        if self.tax_mode == self.Mode.INTER:
            self.igst_paise, self.cgst_paise, self.sgst_paise = tax, 0, 0
        else:
            half = tax // 2
            self.cgst_paise, self.sgst_paise, self.igst_paise = half, tax - half, 0
        gross = self.subtotal_paise + tax
        rounded = int((Decimal(gross) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) * 100
        self.round_off_paise = rounded - gross
        self.total_paise = rounded
        self.received_paise = sum(p.amount_paise for p in self.payments.all()) if self.pk else 0
        if self.status in (self.Status.ISSUED, self.Status.PART, self.Status.PAID):
            if self.received_paise >= self.total_paise and self.total_paise > 0:
                if self.status != self.Status.PAID:
                    self.paid_at = timezone.now()
                self.status = self.Status.PAID
            elif self.received_paise > 0:
                self.status, self.paid_at = self.Status.PART, None
            else:
                self.status, self.paid_at = self.Status.ISSUED, None
        if save:
            self.save()

    @property
    def tax_paise(self):
        return self.cgst_paise + self.sgst_paise + self.igst_paise

    @property
    def balance_paise(self):
        return max(self.total_paise - self.received_paise, 0)

    @property
    def is_open(self):
        return self.status in (self.Status.ISSUED, self.Status.PART)

    @property
    def is_overdue(self):
        return self.is_open and self.due_date < timezone.localdate()

    @property
    def days_overdue(self):
        return (timezone.localdate() - self.due_date).days if self.is_overdue else 0

    @property
    def age_bucket(self) -> str:
        d = self.days_overdue
        if not self.is_open:
            return ""
        if d <= 0:
            return "current"
        if d <= 30:
            return "1-30"
        if d <= 60:
            return "31-60"
        if d <= 90:
            return "61-90"
        return "90+"

    def tax_summary(self):
        """[(rate, taxable, tax)] grouped by GST rate — a mixed-rate invoice prints correctly."""
        out: dict = {}
        for l in self.lines.all():
            r = out.setdefault(l.gst_rate, [0, 0])
            r[0] += l.amount_paise
            r[1] += l.tax_paise
        return [(rate, v[0], v[1]) for rate, v in sorted(out.items())]


class InvoiceLine(TenantModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("contracts.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    description = models.CharField(max_length=255)
    hsn_sac = models.CharField(max_length=8, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    unit = models.CharField(max_length=12, blank=True, default="nos")
    rate_paise = models.BigIntegerField()
    gst_rate = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("18"))
    amount_paise = models.BigIntegerField(default=0)
    tax_paise = models.BigIntegerField(default=0)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def save(self, *args, **kwargs):
        self.amount_paise = int((Decimal(self.quantity) * Decimal(self.rate_paise)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        self.tax_paise = int((Decimal(self.amount_paise) * Decimal(self.gst_rate) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        super().save(*args, **kwargs)


class Payment(TenantModel):
    class Mode(models.TextChoices):
        UPI = "upi", "UPI"
        BANK = "bank", "Bank transfer"
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"
        CARD = "card", "Card"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount_paise = models.BigIntegerField()
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.BANK)
    reference = models.CharField(max_length=80, blank=True, help_text="UTR, UPI ref, cheque number")
    paid_on = models.DateField(default=timezone.localdate)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-paid_on", "-id"]

    def __str__(self):
        return f"{self.get_mode_display()} {self.amount_paise / 100:.2f} on {self.paid_on}"
