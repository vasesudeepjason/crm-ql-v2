from django.core.management.base import BaseCommand
from django.db import connection

from apps.core.rls import apply_rls, tenant_tables


class Command(BaseCommand):
    help = "(Re)apply row-level-security policies to every tenant table (PostgreSQL only)."

    def handle(self, *args, **options):
        tables = tenant_tables()
        if connection.vendor != "postgresql":
            self.stdout.write(self.style.WARNING(f"Database is {connection.vendor}; RLS applies only on PostgreSQL. Tables that would be covered: {', '.join(tables)}"))
            return
        apply_rls()
        self.stdout.write(self.style.SUCCESS(f"RLS applied to {len(tables)} tables: {', '.join(tables)}"))
