from django.contrib import admin

from .models import Activity, Company, Contact, Lead


class AllTenantsAdmin(admin.ModelAdmin):
    """Platform admin sees across tenants (the middleware sets RLS bypass under /platform/)."""

    def get_queryset(self, request):
        return self.model.all_objects.select_related("tenant")


@admin.register(Company)
class CompanyAdmin(AllTenantsAdmin):
    list_display = ("name", "tenant", "city", "phone", "gstin", "owner")
    list_filter = ("tenant",)
    search_fields = ("name", "phone_normalized", "gstin")


@admin.register(Contact)
class ContactAdmin(AllTenantsAdmin):
    list_display = ("name", "company", "tenant", "phone", "is_primary")


@admin.register(Lead)
class LeadAdmin(AllTenantsAdmin):
    list_display = ("name", "tenant", "stage", "status", "owner", "next_follow_up")
    list_filter = ("tenant", "stage", "status")
    search_fields = ("name", "phone_normalized")


@admin.register(Activity)
class ActivityAdmin(AllTenantsAdmin):
    list_display = ("type", "lead", "outcome", "tenant", "created_by", "created_at")
    list_filter = ("tenant", "type")
