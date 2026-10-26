"""
HRMS (foundation document): employee master separate from the login, org structure,
documents, check-in/out attendance, leave types/balances/requests, holiday calendar,
onboarding and exit checklists, letter templates. Bank details, ID numbers and salary
are encrypted per field and shown only to HR and the Owner. Payroll is out of scope.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.crypto import decrypt, encrypt
from apps.core.models import TenantModel

WEEKDAYS = [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]

DEFAULT_ONBOARDING = "Offer letter signed\nID and address proof collected\nBank details collected\nEmail and system access created\nAppointment letter issued\nInduction completed"
DEFAULT_EXIT = "Resignation accepted\nKnowledge handover done\nAssets returned\nSystem access revoked\nFull and final settlement\nRelieving letter issued"
DEFAULT_OFFER = """Dear {name},

We are pleased to offer you the position of {designation} in the {department} team at {company}. Your date of joining will be {join_date} and your annual compensation will be {salary}.

This offer is valid for seven days. Please sign and return a copy to confirm your acceptance.

For {company},

Authorised Signatory"""
DEFAULT_APPOINTMENT = """Dear {name},

With reference to your acceptance of our offer, we are pleased to appoint you as {designation}, {department}, with effect from {join_date}. You will report to {manager}.

Your employment is subject to the policies of {company} as amended from time to time. Your annual compensation is {salary}, payable monthly.

We look forward to working with you.

For {company},

Authorised Signatory"""


class HRSettings(models.Model):
    tenant = models.OneToOneField("tenants.Tenant", on_delete=models.CASCADE, related_name="hr_settings")
    week_off = models.JSONField(default=list, blank=True, help_text="Weekday numbers, Monday=0; default Sunday")
    work_start = models.TimeField(default=time(9, 30))
    work_end = models.TimeField(default=time(18, 30))
    half_day_hours = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("4.0"), help_text="Below this many hours a day counts as half")
    onboarding_items = models.TextField(default=DEFAULT_ONBOARDING, help_text="One per line")
    exit_items = models.TextField(default=DEFAULT_EXIT, help_text="One per line")
    offer_letter = models.TextField(default=DEFAULT_OFFER)
    appointment_letter = models.TextField(default=DEFAULT_APPOINTMENT)

    def __str__(self):
        return f"HR settings · {self.tenant}"

    @classmethod
    def for_tenant(cls, tenant) -> "HRSettings":
        obj, created = cls.objects.get_or_create(tenant=tenant, defaults={"week_off": [6]})
        return obj

    @property
    def onboarding_list(self):
        return [x.strip() for x in self.onboarding_items.splitlines() if x.strip()]

    @property
    def exit_list(self):
        return [x.strip() for x in self.exit_items.splitlines() if x.strip()]

    def is_week_off(self, d: date) -> bool:
        return d.weekday() in (self.week_off or [6])


class Department(TenantModel):
    name = models.CharField(max_length=80)
    head = models.ForeignKey("Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Employee(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        NOTICE = "notice", "On notice"
        EXITED = "exited", "Exited"

    class Type(models.TextChoices):
        FULL = "full_time", "Full-time"
        PART = "part_time", "Part-time"
        CONTRACT = "contract", "Contract"
        INTERN = "intern", "Intern"

    code = models.CharField(max_length=12, blank=True, db_index=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="employee", help_text="Optional login; staff without computer access are still recorded")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    designation = models.CharField(max_length=80, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="employees")
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    employment_type = models.CharField(max_length=10, choices=Type.choices, default=Type.FULL)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    join_date = models.DateField(default=timezone.localdate)
    exit_date = models.DateField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=12, blank=True, choices=[("", "—"), ("female", "Female"), ("male", "Male"), ("other", "Other")])
    address = models.TextField(blank=True)
    photo = models.ImageField(upload_to="hr/photos/", blank=True)
    emergency_name = models.CharField(max_length=120, blank=True)
    emergency_phone = models.CharField(max_length=30, blank=True)
    emergency_relation = models.CharField(max_length=40, blank=True)
    # Encrypted at rest (section 9). Use the sensitive() / set_sensitive() helpers.
    bank_name_enc = models.CharField(max_length=255, blank=True, editable=False)
    bank_account_enc = models.CharField(max_length=255, blank=True, editable=False)
    bank_ifsc_enc = models.CharField(max_length=255, blank=True, editable=False)
    pan_enc = models.CharField(max_length=255, blank=True, editable=False)
    aadhaar_enc = models.CharField(max_length=255, blank=True, editable=False)
    uan_enc = models.CharField(max_length=255, blank=True, editable=False)
    salary_enc = models.CharField(max_length=255, blank=True, editable=False, help_text="Annual CTC in paise, encrypted")
    checklist = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    SENSITIVE = ("bank_name", "bank_account", "bank_ifsc", "pan", "aadhaar", "uan", "salary")

    class Meta:
        ordering = ["first_name", "last_name"]
        indexes = [models.Index(fields=["tenant", "status"]), models.Index(fields=["tenant", "code"])]

    def __str__(self):
        return self.name

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_active(self):
        return self.status != self.Status.EXITED

    def save(self, *args, **kwargs):
        if not self.code:
            from apps.core.tenancy import get_current_tenant_id

            n = Employee.all_objects.filter(tenant_id=self.tenant_id or get_current_tenant_id()).count() + 1
            self.code = f"EMP-{n:04d}"
        super().save(*args, **kwargs)

    def sensitive(self, field: str) -> str:
        return decrypt(getattr(self, f"{field}_enc"))

    def set_sensitive(self, field: str, value: str):
        setattr(self, f"{field}_enc", encrypt((value or "").strip()))

    @property
    def salary_paise(self) -> int | None:
        v = self.sensitive("salary")
        return int(v) if v.isdigit() else None

    def checklist_state(self, kind: str, items: list[str]) -> list[tuple[str, bool]]:
        done = self.checklist.get(kind, {})
        return [(i, bool(done.get(i))) for i in items]


def doc_path(instance, filename):
    return f"hr/docs/{instance.tenant_id}/{instance.employee_id}/{timezone.now():%Y%m%d%H%M%S}-{filename}"


class EmployeeDocument(TenantModel):
    class Kind(models.TextChoices):
        ID = "id", "ID proof"
        ADDRESS = "address", "Address proof"
        OFFER = "offer", "Offer letter"
        APPOINTMENT = "appointment", "Appointment letter"
        CONTRACT = "contract", "Contract / NDA"
        CERT = "certificate", "Certificate"
        OTHER = "other", "Other"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField(max_length=120)
    file = models.FileField(upload_to=doc_path)
    expires_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["kind", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_expired(self):
        return bool(self.expires_on and self.expires_on < timezone.localdate())


class Holiday(TenantModel):
    date = models.DateField()
    name = models.CharField(max_length=80)
    optional = models.BooleanField(default=False, help_text="Restricted holiday: staff may take it or not")

    class Meta:
        ordering = ["date"]
        unique_together = [("tenant", "date", "name")]

    def __str__(self):
        return f"{self.date:%d %b} {self.name}"


class Attendance(TenantModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        HALF = "half_day", "Half day"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "On leave"
        WFH = "wfh", "Work from home"

    class Source(models.TextChoices):
        SELF = "self", "Check-in"
        FIELD = "field", "Field session"
        HR = "hr", "Marked by HR"
        LEAVE = "leave", "Approved leave"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField(db_index=True)
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PRESENT)
    source = models.CharField(max_length=6, choices=Source.choices, default=Source.SELF)
    note = models.CharField(max_length=160, blank=True)

    class Meta:
        unique_together = [("tenant", "employee", "date")]
        ordering = ["-date"]

    @property
    def hours(self) -> Decimal | None:
        if self.check_in and self.check_out:
            return Decimal((self.check_out - self.check_in).total_seconds() / 3600).quantize(Decimal("0.1"))
        return None

    @property
    def code(self) -> str:
        return {"present": "P", "half_day": "H", "absent": "A", "leave": "L", "wfh": "W"}.get(self.status, "?")


class LeaveType(TenantModel):
    name = models.CharField(max_length=60)
    code = models.CharField(max_length=6)
    days_per_year = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("12"))
    paid = models.BooleanField(default=True)
    carry_forward = models.BooleanField(default=False)
    color = models.CharField(max_length=10, default="brand", choices=[("brand", "Navy"), ("green", "Green"), ("yellow", "Amber"), ("red", "Red"), ("info", "Blue"), ("grey", "Grey")])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LeaveAdjustment(TenantModel):
    """HR credit or debit against a balance: opening balance, carry forward, encashment."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_adjustments")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="+")
    year = models.PositiveSmallIntegerField(help_text="Financial year start, e.g. 2026 for FY 2026-27")
    days = models.DecimalField(max_digits=5, decimal_places=1)
    note = models.CharField(max_length=160, blank=True)


class LeaveRequest(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="requests")
    start_date = models.DateField()
    end_date = models.DateField()
    half_day = models.BooleanField(default=False)
    days = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("1"))
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [models.Index(fields=["tenant", "status", "start_date"])]

    def __str__(self):
        return f"{self.employee} · {self.leave_type.code} {self.start_date:%d %b}"

    @property
    def is_open(self):
        return self.status == self.Status.PENDING

    @property
    def fy(self) -> int:
        return self.start_date.year if self.start_date.month >= 4 else self.start_date.year - 1

    def dates(self):
        d = self.start_date
        while d <= self.end_date:
            yield d
            d += timedelta(days=1)
