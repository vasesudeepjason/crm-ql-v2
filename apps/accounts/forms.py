from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from .roles import ROLE_CHOICES


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"placeholder": "you@company.in", "autocomplete": "email", "autofocus": True})
        self.fields["password"].widget.attrs.update({"placeholder": "Your password", "autocomplete": "current-password"})


class EmailForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "you@company.in", "autocomplete": "email", "autofocus": True}))

    def clean_email(self):
        return self.cleaned_data["email"].lower()


class CodeForm(forms.Form):
    code = forms.CharField(min_length=6, max_length=7, widget=forms.TextInput(attrs={"placeholder": "6-digit code", "inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True}))


class InviteForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "colleague@company.in"}))
    role = forms.ChoiceField(choices=[c for c in ROLE_CHOICES if c[0] != "owner"])
    manager = forms.IntegerField(required=False)

    def clean_email(self):
        return self.cleaned_data["email"].lower()


class AcceptInviteForm(forms.Form):
    name = forms.CharField(max_length=120, widget=forms.TextInput(attrs={"placeholder": "Your full name", "autofocus": True}))
    password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={"placeholder": "Choose a password (10+ characters)", "autocomplete": "new-password"}))

    def __init__(self, *args, needs_password=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.needs_password = needs_password

    def clean_password(self):
        pw = self.cleaned_data.get("password") or ""
        if self.needs_password:
            if not pw:
                raise forms.ValidationError("Choose a password.")
            validate_password(pw)
        return pw


class PasswordChangeForm(forms.Form):
    current = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    new = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_current(self):
        if not self.user.check_password(self.cleaned_data["current"]):
            raise forms.ValidationError("Current password is wrong.")
        return self.cleaned_data["current"]

    def clean_new(self):
        validate_password(self.cleaned_data["new"], self.user)
        return self.cleaned_data["new"]
