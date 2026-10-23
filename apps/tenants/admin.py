from django.contrib import admin

from .models import Plan, SupportSession, Tenant


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "price_paise_month", "users_included", "storage_gb")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "plan", "status", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("name", "slug")


@admin.register(SupportSession)
class SupportSessionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "staff", "reason", "started_at", "expires_at", "ended_at", "end_reason")
    list_filter = ("tenant", "end_reason")
    readonly_fields = ("started_at",)
