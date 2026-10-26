from django.urls import path

from . import views

app_name = "hr"
urlpatterns = [
    path("", views.index, name="index"),
    path("me/", views.me, name="me"),
    path("me/punch/", views.punch, name="punch"),
    path("me/leave/", views.leave_apply, name="leave_apply"),
    path("leave/", views.leave, name="leave"),
    path("leave/<int:pk>/decide/", views.leave_decide, name="leave_decide"),
    path("leave/<int:pk>/cancel/", views.leave_cancel, name="leave_cancel"),
    path("attendance/", views.attendance, name="attendance"),
    path("attendance/mark/", views.attendance_mark, name="attendance_mark"),
    path("attendance/export.csv", views.attendance_export, name="attendance_export"),
    path("settings/", views.hr_settings, name="settings"),
    path("settings/leave-types/new/", views.leave_type_form, name="leave_type_new"),
    path("settings/leave-types/<int:pk>/", views.leave_type_form, name="leave_type_edit"),
    path("settings/holidays/new/", views.holiday_form, name="holiday_new"),
    path("settings/holidays/<int:pk>/", views.holiday_form, name="holiday_edit"),
    path("settings/departments/new/", views.department_form, name="department_new"),
    path("settings/departments/<int:pk>/", views.department_form, name="department_edit"),
    path("employees/new/", views.employee_form, name="employee_new"),
    path("employees/<int:pk>/", views.employee_detail, name="employee"),
    path("employees/<int:pk>/edit/", views.employee_form, name="employee_edit"),
    path("employees/<int:pk>/checklist/", views.checklist_toggle, name="checklist"),
    path("employees/<int:pk>/documents/new/", views.doc_upload, name="doc_upload"),
    path("employees/<int:pk>/documents/<int:doc_id>/", views.doc_file, name="doc_file"),
    path("employees/<int:pk>/documents/<int:doc_id>/delete/", views.doc_delete, name="doc_delete"),
    path("employees/<int:pk>/leave/", views.leave_apply, name="leave_for"),
    path("employees/<int:pk>/leave/adjust/", views.leave_adjust, name="leave_adjust"),
    path("employees/<int:pk>/letter/<slug:kind>/", views.letter, name="letter"),
]
