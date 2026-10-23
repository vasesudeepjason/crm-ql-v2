from django.urls import path

from . import views

app_name = "imports"
urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.new, name="new"),
    path("template/<slug:entity>.csv", views.template_csv, name="template"),
    path("<int:pk>/map/", views.map_columns, name="map"),
    path("<int:pk>/review/", views.review, name="review"),
    path("<int:pk>/rows/", views.rows, name="rows"),
    path("<int:pk>/errors.csv", views.errors_csv, name="errors"),
    path("<int:pk>/commit/", views.commit, name="commit"),
    path("<int:pk>/rollback/", views.rollback_job, name="rollback"),
]
