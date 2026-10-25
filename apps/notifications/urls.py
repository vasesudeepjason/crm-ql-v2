from django.urls import path

from . import views

app_name = "notifications"
urlpatterns = [
    path("", views.index, name="index"),
    path("read-all/", views.read_all, name="read_all"),
    path("<int:pk>/", views.open_one, name="open"),
    path("prefs/", views.prefs, name="prefs"),
]
