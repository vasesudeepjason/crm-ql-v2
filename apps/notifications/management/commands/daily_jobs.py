"""
Morning jobs, per tenant: close stale field sessions, follow-up digests, stale-approval
digests. Schedule once a day (cron 07:30 IST, or rq-scheduler):

    python manage.py daily_jobs
"""
from django.core.management.base import BaseCommand

from apps.core.tenancy import tenant_context
from apps.field.services import close_stale_sessions
from apps.notifications.services import follow_up_digest, stale_approvals_digest
from apps.tasks.services import tasks_due_digest
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Run the daily background jobs for every active tenant."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, help="only this tenant id")

    def handle(self, *args, **options):
        qs = Tenant.objects.exclude(status__in=["suspended", "closed"])
        if options.get("tenant"):
            qs = qs.filter(pk=options["tenant"])
        for t in qs:
            with tenant_context(t.id):
                closed = close_stale_sessions(t.timezone)
            f = follow_up_digest(t.id)
            s = stale_approvals_digest(t.id)
            k = tasks_due_digest(t.id)
            self.stdout.write(f"{t.name}: sessions closed {closed}, follow-up digests {f}, task digests {k}, stale-approval digests {s}")
        self.stdout.write(self.style.SUCCESS("Daily jobs done."))
