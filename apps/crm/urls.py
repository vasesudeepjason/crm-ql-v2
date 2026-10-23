from django.urls import path

from . import views

app_name = "crm"
urlpatterns = [
    path("leads/", views.lead_list, name="leads"),
    path("companies/", views.company_list, name="companies"),
]
