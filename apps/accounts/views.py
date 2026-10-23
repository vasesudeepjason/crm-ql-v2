from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"placeholder": "you@company.in", "autocomplete": "email", "autofocus": True})
        self.fields["password"].widget.attrs.update({"placeholder": "Your password", "autocomplete": "current-password"})


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True
