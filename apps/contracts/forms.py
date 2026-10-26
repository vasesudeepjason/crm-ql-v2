from datetime import timedelta
from decimal import Decimal

from django import forms

from apps.crm.models import Company, Contact, Lead

from .models import Contract, Product


class RupeeField(forms.DecimalField):
    """Shown in rupees, stored in paise."""

    def __init__(self, **kw):
        kw.setdefault("min_value", 0)
        kw.setdefault("decimal_places", 2)
        kw.setdefault("max_digits", 14)
        kw.setdefault("widget", forms.NumberInput(attrs={"inputmode": "decimal", "step": "0.01", "placeholder": "e.g. 48000"}))
        super().__init__(**kw)

    @staticmethod
    def to_paise(v) -> int:
        return int((Decimal(v or 0) * 100).quantize(Decimal("1")))


class ProductForm(forms.ModelForm):
    price = RupeeField(label="Default price (₹, before GST)")

    class Meta:
        model = Product
        fields = ["name", "kind", "hsn_sac", "gst_rate", "default_cycle", "description", "is_active"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. AMC Gold — annual maintenance", "autofocus": True}), "hsn_sac": forms.TextInput(attrs={"placeholder": "998713", "inputmode": "numeric"}), "description": forms.TextInput(attrs={"placeholder": "What the customer gets"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk:
            self.fields["price"].initial = Decimal(self.instance.default_price_paise) / 100

    def clean_hsn_sac(self):
        v = (self.cleaned_data.get("hsn_sac") or "").strip()
        if v and (not v.isdigit() or len(v) not in (4, 6, 8)):
            raise forms.ValidationError("HSN/SAC codes are 4, 6 or 8 digits.")
        return v

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.default_price_paise = RupeeField.to_paise(self.cleaned_data["price"])
        if commit:
            obj.save()
        return obj


class ContractForm(forms.ModelForm):
    amount = RupeeField(label="Amount per cycle (₹, before GST)")

    class Meta:
        model = Contract
        fields = ["company", "product", "title", "start_date", "end_date", "billing_cycle", "auto_renew", "owner", "contact", "contact_email", "notes", "lead"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Scope, SLAs, anything the renewal owner should know"}),
                   "title": forms.TextInput(attrs={"placeholder": "Optional — e.g. AMC for 3 depots"}), "lead": forms.HiddenInput(), "contact_email": forms.EmailInput(attrs={"placeholder": "client@company.in"})}

    def __init__(self, *a, users, **kw):
        super().__init__(*a, **kw)
        self.fields["company"].queryset = Company.objects.order_by("name")
        self.fields["company"].empty_label = "— pick a company —"
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["product"].empty_label = "— pick from the catalogue —"
        self.fields["owner"].queryset = self.fields["owner"].queryset.filter(pk__in=[u.pk for u in users])
        self.fields["owner"].empty_label = "— renewal owner —"
        self.fields["lead"].queryset = Lead.objects.all()
        self.fields["lead"].required = False
        company_id = self.data.get("company") or (self.instance.company_id if self.instance.pk else None) or (self.initial.get("company"))
        self.fields["contact"].queryset = Contact.objects.filter(company_id=company_id) if company_id else Contact.objects.none()
        self.fields["contact"].empty_label = "— none —"
        if self.instance.pk:
            self.fields["amount"].initial = Decimal(self.instance.amount_paise) / 100

    def clean(self):
        d = super().clean()
        if d.get("start_date") and d.get("end_date") and d["end_date"] < d["start_date"]:
            self.add_error("end_date", "End date is before the start date.")
        if d.get("contact") and not d.get("contact_email") and d["contact"].email:
            d["contact_email"] = d["contact"].email
        return d

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.amount_paise = RupeeField.to_paise(self.cleaned_data["amount"])
        if commit:
            obj.save()
        return obj


class RenewForm(forms.Form):
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = RupeeField(label="Amount per cycle (₹)")
    auto_renew = forms.BooleanField(required=False)
    notes = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"placeholder": "e.g. price revised 8%, PO 4471"}))

    def __init__(self, *a, contract: Contract, **kw):
        super().__init__(*a, **kw)
        s, e = contract.next_term()
        self.fields["start_date"].initial, self.fields["end_date"].initial = s, e
        self.fields["amount"].initial = Decimal(contract.amount_paise) / 100
        self.fields["auto_renew"].initial = contract.auto_renew

    def clean(self):
        d = super().clean()
        if d.get("start_date") and d.get("end_date") and d["end_date"] <= d["start_date"]:
            self.add_error("end_date", "The new term must end after it starts.")
        return d


class LapseForm(forms.Form):
    reason = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"placeholder": "Why did they not renew? (kept for win-back)", "autofocus": True}))
