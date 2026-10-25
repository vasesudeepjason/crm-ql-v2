from django import forms

from apps.crm.models import Company, Lead

from .models import Visit


class VisitForm(forms.ModelForm):
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False, empty_label="— Pick a company —")
    new_company = forms.CharField(required=False, max_length=160, widget=forms.TextInput(attrs={"placeholder": "…or a new company name"}))
    lead = forms.ModelChoiceField(queryset=Lead.objects.none(), required=False, empty_label="— Link a lead (optional) —")
    lat = forms.FloatField(required=False, widget=forms.HiddenInput)
    lng = forms.FloatField(required=False, widget=forms.HiddenInput)
    accuracy = forms.FloatField(required=False, widget=forms.HiddenInput)
    mock_location = forms.BooleanField(required=False, widget=forms.HiddenInput)
    offline_id = forms.CharField(required=False, widget=forms.HiddenInput)
    captured_at = forms.DateTimeField(required=False, widget=forms.HiddenInput, input_formats=["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"])

    class Meta:
        model = Visit
        fields = ["company", "lead", "contact_name", "outcome", "notes", "next_follow_up", "duration_minutes", "photo"]
        widgets = {
            "contact_name": forms.TextInput(attrs={"placeholder": "Who you met"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "What was discussed, what they asked for"}),
            "next_follow_up": forms.DateInput(attrs={"type": "date"}),
            "duration_minutes": forms.NumberInput(attrs={"placeholder": "min", "inputmode": "numeric"}),
            "photo": forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "environment"}),
        }

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["company"].queryset = Company.objects.order_by("name")
        self.fields["lead"].queryset = Lead.objects.filter(owner=request.user).exclude(stage__in=["won", "lost"]).select_related("company").order_by("name")
        self.fields["outcome"].choices = [("", "— Outcome —"), *Visit.Outcome.choices]

    def clean(self):
        d = super().clean()
        if not (d.get("company") or d.get("new_company") or d.get("lead") or d.get("contact_name")):
            raise forms.ValidationError("Say who you visited: pick a company or lead, or type a name.")
        if d.get("outcome") in (Visit.Outcome.INTERESTED, Visit.Outcome.FOLLOW_UP) and not d.get("next_follow_up"):
            self.add_error("next_follow_up", "Set the next follow-up date for this outcome.")
        return d
