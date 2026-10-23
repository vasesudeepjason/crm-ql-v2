from django.contrib.auth import views as auth_views
from django.urls import path

from . import settings_views, views

app_name = "accounts"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("otp/", views.otp_request, name="otp"),
    path("otp/verify/", views.otp_verify, name="otp_verify"),
    path("2fa/", views.twofa, name="twofa"),
    path("invite/<str:token>/", views.accept_invite, name="invite_accept"),
    # workspace settings
    path("settings/profile/", settings_views.profile, name="profile"),
    path("settings/profile/password/", settings_views.change_password, name="change_password"),
    path("settings/profile/totp/setup/", settings_views.totp_setup, name="totp_setup"),
    path("settings/profile/totp/disable/", settings_views.totp_disable, name="totp_disable"),
    path("settings/users/", settings_views.users, name="users"),
    path("settings/users/invite/", settings_views.invite, name="invite"),
    path("settings/users/<int:pk>/role/", settings_views.change_role, name="change_role"),
    path("settings/users/<int:pk>/toggle/", settings_views.toggle_active, name="toggle_active"),
    path("settings/invites/<int:pk>/resend/", settings_views.resend_invite, name="resend_invite"),
    path("settings/invites/<int:pk>/revoke/", settings_views.revoke_invite, name="revoke_invite"),
]
