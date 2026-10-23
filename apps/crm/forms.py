from __future__ import annotations

from django import forms

from apps.accounts.models import Membership
from apps.core.phones import normalize_phone

from .models import Activity, Company, Lead

OWNER_ROLES = ("owner", "admin", "manager", "sales", "telecaller", "field")


def owner_choices(request):
    """Who a lead can be assigned to, given the signed-in user's assign scope."""
    m = request.membership
    qs = Membership.objects.filter(tenant=request.tenant, is_active=True, role__in=OWNER_ROLES).select_related("user").order_by("user__name")
    scope = m.scope_for("leads.assign")
    if scope == "all":
        pass
    elif scope == "team":
        qs = qs.filter(user_id__in=m.team_user_ids())
    else:
        qs = qs.filter(user_id=m.user_id)
    return [(x.user_id, f"{x.user.name} · {x.role_name}") for x in qs]


class LeadForm(forms.ModelForm):
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False, empty_label="— No company yet —")
    new_company = forms.CharField(required=False, max_length=160, widget=forms.TextInput(attrs={"placeholder": "…or type a new company name"}))
    owner = forms.ChoiceField(required=False)
    est_value = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, widget=forms.NumberInput(attrs={"placeholder": "e.g. 48000", "inputmode": "decimal"}))
    tags_text = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "AMC, Priority"}))
    create_anyway = forms.BooleanField(required=False)

    class Meta:
        model = Lead
        fields = ["name", "phone", "email", "stage", "source", "next_follow_up", "consent_note", "do_not_contact"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Person or company this lead is about", "autofocus": True}),
            "phone": forms.TextInput(attrs={"placeholder": "+91 98480 12345", "inputmode": "tel", "autocomplete": "off"}),
            "email": forms.EmailInput(attrs={"placeholder": "name@company.in"}),
            "source": forms.TextInput(attrs={"placeholder": "Referral, IndiaMART, Walk-in…", "list": "sources"}),
            "next_follow_up": forms.DateInput(attrs={"type": "date"}),
            "consent_note": forms.TextInput(attrs={"placeholder": "How and why this person's details were captured (DPDP)"}),
        }

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["company"].queryset = Company.objects.order_by("name")
        choices = owner_choices(request)
        self.fields["owner"].choices = [("", "— Unassigned —"), *choices] if request.membership.scope_for("leads.assign") in ("all", "team") else choices
        self.fields["stage"].choices = [c for c in Lead.Stage.choices if c[0] not in ("won", "lost")] if self.instance.pk is None else Lead.Stage.choices
        if self.instance.pk:
            self.fields["owner"].initial = self.instance.owner_id
            self.fields["est_value"].initial = (self.instance.est_value_paise or 0) / 100 if self.instance.est_value_paise is not None else None
            self.fields["tags_text"].initial = ", ".join(self.instance.tags or [])
        elif len(choices) == 1:
            self.fields["owner"].initial = choices[0][0]

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone and not normalize_phone(phone):
            raise forms.ValidationError("That doesn't look like a valid phone number.")
        return phone

    def clean(self):
        data = super().clean()
        if not data.get("phone") and not data.get("email"):
            raise forms.ValidationError("Give at least a phone number or an email so the lead can be contacted and checked for duplicates.")
        if data.get("new_company") and data.get("company"):
            self.add_error("new_company", "Pick an existing company or type a new one, not both.")
        if data.get("stage") == Lead.Stage.WON and not (data.get("company") or data.get("new_company")):
            self.add_error("stage", "A lead can move to Won only with a company attached.")
        return data

    def save(self, commit=True):
        lead = super().save(commit=False)
        d = self.cleaned_data
        if d.get("new_company"):
            c = Company(name=d["new_company"].strip(), owner_id=int(d["owner"]) if d.get("owner") else self.request.user.id)
            c.created_by = self.request.user
            c.save()
            lead.company = c
        elif "company" in d:
            lead.company = d.get("company")
        lead.owner_id = int(d["owner"]) if d.get("owner") else None
        lead.est_value_paise = int(round(float(d["est_value"]) * 100)) if d.get("est_value") is not None else None
        lead.tags = [t.strip() for t in (d.get("tags_text") or "").split(",") if t.strip()]
        if lead.pk is None:
            lead.created_by = self.request.user
        if commit:
            lead.save()
        return lead


class StageForm(forms.Form):
    stage = forms.ChoiceField(choices=Lead.Stage.choices)
    note = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"placeholder": "Note (optional)"}))
    lost_reason = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"placeholder": "Why was it lost?"}))
    next_follow_up = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        d = super().clean()
        if d.get("stage") == Lead.Stage.LOST and not (d.get("lost_reason") or "").strip():
            self.add_error("lost_reason", "A lost lead needs a reason.")
        return d


class ActivityForm(forms.ModelForm):
    duration_minutes = forms.IntegerField(required=False, min_value=0, max_value=600, widget=forms.NumberInput(attrs={"placeholder": "min"}))
    next_follow_up = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Activity
        fields = ["type", "outcome", "notes", "due_at"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2, "placeholder": "What happened?"}), "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def clean(self):
        d = super().clean()
        if d.get("type") == Activity.Type.CALL and not d.get("outcome"):
            self.add_error("outcome", "Pick the call outcome.")
        if d.get("type") == Activity.Type.TASK and not d.get("due_at"):
            self.add_error("due_at", "A task needs a due date.")
        return d
