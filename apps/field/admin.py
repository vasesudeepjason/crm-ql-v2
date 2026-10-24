from django.contrib import admin

from .models import FieldSession, Visit


@admin.register(FieldSession)
class FieldSessionAdmin(admin.ModelAdmin):
    list_display = ("executive", "tenant", "started_at", "ended_at", "end_source")
    list_filter = ("tenant", "end_source")

    def get_queryset(self, request):
        return FieldSession.all_objects.select_related("tenant", "executive")


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("__str__", "executive", "tenant", "captured_at", "outcome", "status", "accuracy")
    list_filter = ("tenant", "status", "outcome")

    def get_queryset(self, request):
        return Visit.all_objects.select_related("tenant", "executive", "company")
