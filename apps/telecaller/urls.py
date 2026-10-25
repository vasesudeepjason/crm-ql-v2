from django.urls import path

from . import views

app_name = "telecaller"
urlpatterns = [
    path("", views.desk, name="desk"),
    path("log/<int:pk>/", views.log_form, name="log_form"),
    path("log/<int:pk>/save/", views.log_call, name="log_call"),
    path("team/", views.team, name="team"),
    path("team/<int:user_id>/target/", views.set_target, name="set_target"),
]
