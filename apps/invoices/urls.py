from django.urls import path

from . import views

app_name = "invoices"
urlpatterns = [
    path("", views.index, name="index"),
    path("ageing/", views.ageing, name="ageing"),
    path("new/", views.new, name="new"),
    path("export/<slug:fmt>/", views.export, name="export"),
    path("settings/", views.settings_save, name="settings"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/issue/", views.issue, name="issue"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
    path("<int:pk>/delete/", views.delete_draft, name="delete_draft"),
    path("<int:pk>/pdf/", views.pdf, name="pdf"),
    path("<int:pk>/send/", views.send, name="send"),
    path("<int:pk>/payments/new/", views.payment_add, name="payment_add"),
    path("<int:pk>/payments/<int:payment_id>/delete/", views.payment_delete, name="payment_delete"),
]
