from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("platform/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("approvals/", include("apps.approvals.urls")),
    path("crm/", include("apps.crm.urls")),
    path("imports/", include("apps.imports.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.dashboard.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "QuantumLoop platform admin"
admin.site.site_title = "QuantumLoop"
