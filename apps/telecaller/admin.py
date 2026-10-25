from django.contrib import admin

from .models import CallTarget


@admin.register(CallTarget)
class CallTargetAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "calls_per_day", "leads_per_day", "set_by", "updated_at")
    list_filter = ("tenant",)

    def get_queryset(self, request):
        return CallTarget.all_objects.select_related("tenant", "user")
