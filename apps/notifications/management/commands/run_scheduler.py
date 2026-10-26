"""
Tiny in-process scheduler for the daily jobs, so production needs no cron entry:

    python manage.py run_scheduler            # blocks; runs daily_jobs at 07:30 Asia/Kolkata every day

Runs once immediately if the day's run was missed (e.g. after a redeploy past 07:30).
"""
import time
import zoneinfo
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Run daily_jobs every morning at 07:30 in the default tenant timezone."

    def add_arguments(self, parser):
        parser.add_argument("--at", default="07:30", help="HH:MM local time")
        parser.add_argument("--once", action="store_true", help="run now and exit (for testing)")

    def handle(self, *args, **options):
        tz = zoneinfo.ZoneInfo(settings.DEFAULT_TENANT_TIMEZONE)
        hh, mm = (int(x) for x in options["at"].split(":"))
        if options["once"]:
            call_command("daily_jobs")
            return
        last_run_day = None
        while True:
            now = timezone.now().astimezone(tz)
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now >= due and last_run_day != now.date():
                self.stdout.write(f"{now:%Y-%m-%d %H:%M} running daily_jobs")
                try:
                    call_command("daily_jobs")
                except Exception as e:  # keep the loop alive; the next day retries
                    self.stderr.write(f"daily_jobs failed: {e}")
                last_run_day = now.date()
            nxt = due if now < due else (due + timedelta(days=1))
            time.sleep(min(max((nxt - timezone.now().astimezone(tz)).total_seconds(), 30), 3600))
