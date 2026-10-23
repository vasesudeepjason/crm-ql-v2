from django.urls import path

from . import views

app_name = "crm"
urlpatterns = [
    path("leads/", views.lead_list, name="leads"),
    path("leads/new/", views.lead_create, name="lead_create"),
    path("leads/check-duplicate/", views.check_duplicate, name="check_duplicate"),
    path("leads/kanban/", views.kanban, name="kanban"),
    path("leads/<int:pk>/", views.lead_detail, name="lead_detail"),
    path("leads/<int:pk>/edit/", views.lead_edit, name="lead_edit"),
    path("leads/<int:pk>/stage/", views.lead_stage, name="lead_stage"),
    path("leads/<int:pk>/activity/", views.activity_add, name="activity_add"),
    path("leads/<int:pk>/task/<int:activity_id>/done/", views.task_done, name="task_done"),
    path("leads/<int:pk>/move/", views.kanban_move, name="kanban_move"),
    path("companies/", views.company_list, name="companies"),
]
