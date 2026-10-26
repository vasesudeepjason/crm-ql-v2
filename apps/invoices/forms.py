from decimal import Decimal, InvalidOperation

from django import forms

from apps.contracts.forms import RupeeField
from apps.contracts.models import GST_RATES

from .gst import STATE_CHOICES
from .models import InvoiceSettings, Payment


class InvoiceSettingsForm(forms.ModelForm):
    class Meta:
        model = InvoiceSettings
        fields = ["legal_name", "address", "state_code", "pan", "email", "phone", "website", "logo", "bank_account_name", "bank_name", "bank_account_no", "bank_ifsc", "upi_id", "series_prefix", "due_days", "default_notes", "default_terms"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3}), "default_terms": forms.Textarea(attrs={"rows": 4}), "series_prefix": forms.TextInput(attrs={"maxlength": 10, "style": "text-transform:uppercase"})}

    def clean_series_prefix(self):
        v = (self.cleaned_data["series_prefix"] or "INV").strip().upper().replace("/", "-")
        return v[:10] or "INV"

    def clean_pan(self):
        v = (self.cleaned_data.get("pan") or "").strip().upper()
        if v and (len(v) != 10 or not v[:5].isalpha() or not v[5:9].isdigit() or not v[9].isalpha()):
            raise forms.ValidationError("PAN looks wrong (AAAAA9999A).")
        return v

    def clean_bank_ifsc(self):
        v = (self.cleaned_data.get("bank_ifsc") or "").strip().upper()
        if v and (len(v) != 11 or v[4] != "0"):
            raise forms.ValidationError("IFSC is 11 characters with a 0 in the fifth place.")
        return v


class InvoiceHeadForm(forms.Form):
    """The non-line part of an invoice. Lines are parsed separately from the POST arrays."""

    issue_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    bill_to_name = forms.CharField(max_length=160)
    bill_to_address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    bill_to_gstin = forms.CharField(required=False, max_length=15)
    bill_to_state = forms.ChoiceField(required=False, choices=[("", "—")] + STATE_CHOICES)
    bill_to_email = forms.EmailField(required=False)
    place_of_supply = forms.ChoiceField(choices=STATE_CHOICES)
    ship_to_name = forms.CharField(required=False, max_length=160)
    ship_to_address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    period_start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    period_end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, max_length=255)
    terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_bill_to_gstin(self):
        v = (self.cleaned_data.get("bill_to_gstin") or "").strip().upper()
        if v:
            from apps.core.phones import validate_gstin

            if not validate_gstin(v):
                raise forms.ValidationError("Buyer GSTIN fails the format or checksum.")
        return v

    def clean(self):
        d = super().clean()
        if d.get("issue_date") and d.get("due_date") and d["due_date"] < d["issue_date"]:
            self.add_error("due_date", "Due date is before the invoice date.")
        return d


def parse_lines(post) -> tuple[list[dict], str | None]:
    """Reads line_desc[], line_hsn[], line_qty[], line_rate[], line_gst[], line_product[]. Returns (lines, error)."""
    descs = post.getlist("line_desc")
    out = []
    for i, desc in enumerate(descs):
        desc = (desc or "").strip()
        qty_s, rate_s = post.getlist("line_qty")[i] if i < len(post.getlist("line_qty")) else "", post.getlist("line_rate")[i] if i < len(post.getlist("line_rate")) else ""
        if not desc and not rate_s:
            continue
        if not desc:
            return [], f"Line {i + 1} needs a description."
        try:
            qty = Decimal(qty_s or "1")
            rate = Decimal(rate_s or "0")
            gst = Decimal(post.getlist("line_gst")[i] if i < len(post.getlist("line_gst")) else "18")
        except InvalidOperation:
            return [], f"Line {i + 1} has a number that does not parse."
        if qty <= 0 or rate < 0:
            return [], f"Line {i + 1}: quantity must be positive and rate not negative."
        if gst not in [r for r, _ in GST_RATES]:
            return [], f"Line {i + 1}: GST rate must be one of 0, 5, 12, 18, 28."
        hsn = (post.getlist("line_hsn")[i] if i < len(post.getlist("line_hsn")) else "").strip()
        prod = post.getlist("line_product")[i] if i < len(post.getlist("line_product")) else ""
        out.append({"description": desc[:255], "hsn_sac": hsn[:8], "quantity": qty, "rate_paise": RupeeField.to_paise(rate), "gst_rate": gst, "product_id": int(prod) if prod.isdigit() else None, "position": len(out)})
    if not out:
        return [], "Add at least one line."
    return out, None


class PaymentForm(forms.ModelForm):
    amount = RupeeField(label="Amount received (₹)")

    class Meta:
        model = Payment
        fields = ["mode", "reference", "paid_on", "notes"]
        widgets = {"paid_on": forms.DateInput(attrs={"type": "date"}), "reference": forms.TextInput(attrs={"placeholder": "UTR / UPI ref / cheque no."}), "notes": forms.TextInput(attrs={"placeholder": "Optional"})}

    def __init__(self, *a, invoice, **kw):
        super().__init__(*a, **kw)
        self.invoice = invoice
        self.fields["amount"].initial = Decimal(invoice.balance_paise) / 100

    def clean_amount(self):
        v = self.cleaned_data["amount"]
        if v <= 0:
            raise forms.ValidationError("Enter an amount above zero.")
        if RupeeField.to_paise(v) > self.invoice.balance_paise + 100:  # allow a rupee of rounding slack
            raise forms.ValidationError(f"That is more than the balance due ({self.invoice.balance_paise / 100:,.2f}).")
        return v
