from django.urls import path

from . import views

app_name = "dashboard"
urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("offline/", views.offline, name="offline"),
    path("sw.js", views.service_worker, name="sw"),
    path("soon/<slug:module>/", views.coming_soon, name="soon"),
]
