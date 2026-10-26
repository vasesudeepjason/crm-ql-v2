from decimal import Decimal

from django import forms

from apps.accounts.models import Membership
from apps.contracts.forms import RupeeField

from .models import WEEKDAYS, Department, Employee, EmployeeDocument, Holiday, HRSettings, LeaveRequest, LeaveType


class EmployeeForm(forms.ModelForm):
    # sensitive fields are plain form fields, written through set_sensitive()
    bank_name = forms.CharField(required=False, max_length=120)
    bank_account = forms.CharField(required=False, max_length=30)
    bank_ifsc = forms.CharField(required=False, max_length=11)
    pan = forms.CharField(required=False, max_length=10)
    aadhaar = forms.CharField(required=False, max_length=14)
    uan = forms.CharField(required=False, max_length=12)
    salary = RupeeField(required=False, label="Annual CTC (₹)")

    class Meta:
        model = Employee
        fields = ["first_name", "last_name", "email", "phone", "designation", "department", "manager", "user", "employment_type", "status", "join_date", "exit_date", "date_of_birth", "gender", "address", "photo", "emergency_name", "emergency_phone", "emergency_relation", "notes"]
        widgets = {"join_date": forms.DateInput(attrs={"type": "date"}), "exit_date": forms.DateInput(attrs={"type": "date"}), "date_of_birth": forms.DateInput(attrs={"type": "date"}), "address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2}),
                   "first_name": forms.TextInput(attrs={"autofocus": True})}

    def __init__(self, *a, tenant, show_sensitive: bool, **kw):
        super().__init__(*a, **kw)
        self.show_sensitive = show_sensitive
        self.fields["department"].queryset = Department.objects.all()
        self.fields["department"].empty_label = "— none —"
        self.fields["manager"].queryset = Employee.objects.exclude(status="exited").exclude(pk=self.instance.pk or 0)
        self.fields["manager"].empty_label = "— none —"
        used = Employee.objects.exclude(pk=self.instance.pk or 0).exclude(user__isnull=True).values_list("user_id", flat=True)
        self.fields["user"].queryset = self.fields["user"].queryset.filter(memberships__tenant=tenant, memberships__is_active=True).exclude(pk__in=used).order_by("name")
        self.fields["user"].empty_label = "— no login —"
        self.fields["user"].label = "Login"
        if not show_sensitive:
            for f in Employee.SENSITIVE:
                self.fields.pop(f)
        elif self.instance.pk:
            for f in Employee.SENSITIVE:
                if f == "salary":
                    sp = self.instance.salary_paise
                    self.fields["salary"].initial = Decimal(sp) / 100 if sp else None
                else:
                    self.fields[f].initial = self.instance.sensitive(f)

    def clean_pan(self):
        v = (self.cleaned_data.get("pan") or "").strip().upper()
        if v and (len(v) != 10 or not v[:5].isalpha() or not v[5:9].isdigit() or not v[9].isalpha()):
            raise forms.ValidationError("PAN looks wrong (AAAAA9999A).")
        return v

    def clean_aadhaar(self):
        v = (self.cleaned_data.get("aadhaar") or "").replace(" ", "")
        if v and (len(v) != 12 or not v.isdigit()):
            raise forms.ValidationError("Aadhaar is 12 digits.")
        return v

    def clean(self):
        d = super().clean()
        if d.get("exit_date") and d.get("join_date") and d["exit_date"] < d["join_date"]:
            self.add_error("exit_date", "Exit date is before the join date.")
        if d.get("status") == "exited" and not d.get("exit_date"):
            self.add_error("exit_date", "Set the exit date for an exited employee.")
        return d

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.show_sensitive:
            for f in Employee.SENSITIVE:
                if f == "salary":
                    v = self.cleaned_data.get("salary")
                    obj.set_sensitive("salary", str(RupeeField.to_paise(v)) if v is not None else "")
                else:
                    obj.set_sensitive(f, self.cleaned_data.get(f, ""))
        if commit:
            obj.save()
        return obj


class DocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ["kind", "title", "file", "expires_on"]
        widgets = {"expires_on": forms.DateInput(attrs={"type": "date"}), "title": forms.TextInput(attrs={"placeholder": "e.g. Aadhaar card, Offer letter 2026"})}

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Keep documents under 10 MB.")
        if not f.name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".docx", ".doc")):
            raise forms.ValidationError("PDF, JPG, PNG or Word files only.")
        return f


class LeaveForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["leave_type", "start_date", "end_date", "half_day", "reason"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"}), "reason": forms.TextInput(attrs={"placeholder": "Optional"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["leave_type"].queryset = LeaveType.objects.filter(is_active=True)
        self.fields["leave_type"].empty_label = None

    def clean(self):
        d = super().clean()
        if d.get("start_date") and d.get("end_date"):
            if d["end_date"] < d["start_date"]:
                self.add_error("end_date", "End is before start.")
            if d.get("half_day") and d["end_date"] != d["start_date"]:
                self.add_error("half_day", "A half day is a single date.")
        return d


class LeaveTypeForm(forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ["name", "code", "days_per_year", "paid", "carry_forward", "color", "is_active"]


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name", "optional"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "head"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["head"].queryset = Employee.objects.exclude(status="exited")
        self.fields["head"].empty_label = "— none —"


class HRSettingsForm(forms.ModelForm):
    week_off_days = forms.MultipleChoiceField(choices=WEEKDAYS, required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = HRSettings
        fields = ["work_start", "work_end", "half_day_hours", "onboarding_items", "exit_items", "offer_letter", "appointment_letter"]
        widgets = {"work_start": forms.TimeInput(attrs={"type": "time"}), "work_end": forms.TimeInput(attrs={"type": "time"}), "onboarding_items": forms.Textarea(attrs={"rows": 6}), "exit_items": forms.Textarea(attrs={"rows": 6}), "offer_letter": forms.Textarea(attrs={"rows": 9}), "appointment_letter": forms.Textarea(attrs={"rows": 9})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["week_off_days"].initial = [str(x) for x in (self.instance.week_off or [6])]

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.week_off = [int(x) for x in self.cleaned_data.get("week_off_days", [])]
        if commit:
            obj.save()
        return obj
