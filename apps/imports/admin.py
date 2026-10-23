from django.contrib import admin

from .models import ImportJob, ImportRow, SavedMapping


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ("filename", "entity", "source", "tenant", "status", "row_count", "created_by", "created_at", "committed_at")
    list_filter = ("tenant", "entity", "status")

    def get_queryset(self, request):
        return ImportJob.all_objects.select_related("tenant", "created_by")


@admin.register(ImportRow)
class ImportRowAdmin(admin.ModelAdmin):
    list_display = ("job", "row_no", "status", "duplicate_of", "object_id")
    list_filter = ("status",)

    def get_queryset(self, request):
        return ImportRow.all_objects.select_related("job")


@admin.register(SavedMapping)
class SavedMappingAdmin(admin.ModelAdmin):
    list_display = ("name", "entity", "source", "tenant", "updated_at")

    def get_queryset(self, request):
        return SavedMapping.all_objects.all()
