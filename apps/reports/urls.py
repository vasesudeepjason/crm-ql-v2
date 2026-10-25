from django.urls import path

from . import views

app_name = "reports"
urlpatterns = [
    path("", views.index, name="index"),
    path("export/<slug:what>.csv", views.export, name="export"),
]
