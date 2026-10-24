from django.urls import path

from . import views

app_name = "field"
urlpatterns = [
    path("", views.my_day, name="my_day"),
    path("session/start/", views.session_start, name="session_start"),
    path("session/end/", views.session_end, name="session_end"),
    path("ping/", views.ping, name="ping"),
    path("visits/new/", views.visit_new, name="visit_new"),
    path("visits/", views.my_visits, name="visits"),
    path("visits/<int:pk>/photo/", views.visit_photo, name="visit_photo"),
    path("team/", views.team, name="team"),
    path("team/session/<int:pk>/end/", views.end_session_for, name="end_session_for"),
]
