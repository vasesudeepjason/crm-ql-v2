from django.urls import path

from . import views

app_name = "approvals"
urlpatterns = [
    path("", views.queue, name="queue"),
    path("bulk/approve/", views.bulk_approve, name="bulk_approve"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/approve/", views.approve, name="approve"),
    path("<int:pk>/reject/", views.reject, name="reject"),
    path("<int:pk>/info/", views.request_info, name="info"),
]
