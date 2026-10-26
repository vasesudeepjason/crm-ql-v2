from django.urls import path

from . import views

app_name = "tasks"
urlpatterns = [
    path("", views.my_tasks, name="my"),
    path("quick/", views.quick_add, name="quick_add"),
    path("new/", views.task_form, name="new"),
    path("<int:pk>/edit/", views.task_form, name="edit"),
    path("<int:pk>/toggle/", views.toggle, name="toggle"),
    path("<int:pk>/move/", views.move, name="move"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("projects/", views.projects, name="projects"),
    path("projects/new/", views.project_form, name="project_new"),
    path("projects/<int:pk>/", views.project_detail, name="project"),
    path("projects/<int:pk>/edit/", views.project_form, name="project_edit"),
    path("projects/<int:pk>/status/", views.project_status, name="project_status"),
]
