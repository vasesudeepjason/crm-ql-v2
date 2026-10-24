"""Close field sessions still open after day end, per tenant timezone. Run nightly (cron / RQ scheduler)."""
from django.core.management.base import BaseCommand

from apps.core.tenancy import tenant_context
from apps.field.services import close_stale_sessions
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Auto-close stale field sessions for every tenant."

    def handle(self, *args, **options):
        total = 0
        for t in Tenant.objects.all():
            with tenant_context(t.id):
                n = close_stale_sessions(t.timezone)
                total += n
                if n:
                    self.stdout.write(f"{t.name}: closed {n}")
        self.stdout.write(self.style.SUCCESS(f"Closed {total} session(s)."))
