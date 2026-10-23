from django.contrib import admin

from .models import Approval


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "tenant", "status", "submitter", "reviewer", "submitted_at", "decided_at")
    list_filter = ("tenant", "kind", "status")

    def get_queryset(self, request):
        return Approval.all_objects.select_related("tenant", "submitter", "reviewer")
