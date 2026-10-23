from django.contrib import admin

from .models import Plan, Tenant


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "price_paise_month", "users_included", "storage_gb")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "plan", "status", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("name", "slug")
