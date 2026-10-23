from django.urls import path

from . import views

app_name = "tenants"
urlpatterns = [
    path("platform/support/", views.support_console, name="support"),
    path("platform/support/<int:tenant_id>/start/", views.support_start, name="support_start"),
    path("support/leave/", views.support_leave, name="support_leave"),
    path("settings/workspace/", views.workspace, name="workspace"),
    path("settings/workspace/support/<int:pk>/revoke/", views.revoke_support, name="revoke_support"),
    path("settings/workspace/export/", views.export_all, name="export_all"),
    path("settings/workspace/delete-request/", views.request_deletion, name="request_deletion"),
]
