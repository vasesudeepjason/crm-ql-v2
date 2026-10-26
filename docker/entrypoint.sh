#!/bin/sh
# web       → migrate, then gunicorn
# worker    → RQ worker for emails, digests and exports
# scheduler → runs daily_jobs every morning (07:30 in the tenant's default timezone)
# anything else is passed to manage.py (e.g. `seed_demo`, `createsuperuser`, `shell`)
set -e

wait_for_db() {
  python - <<'EOF'
import os, sys, time
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "quantumloop.settings")
django.setup()
from django.db import connection
for i in range(60):
    try:
        connection.ensure_connection()
        sys.exit(0)
    except Exception as e:
        print(f"waiting for database ({e.__class__.__name__})…", flush=True)
        time.sleep(2)
sys.exit("database never became ready")
EOF
}

case "$1" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    python manage.py check --deploy --fail-level ERROR
    exec gunicorn quantumloop.wsgi:application \
      --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}" --threads 2 --timeout 60 \
      --access-logfile - --error-logfile - --forwarded-allow-ips="*"
    ;;
  worker)
    wait_for_db
    exec python manage.py rqworker default
    ;;
  scheduler)
    wait_for_db
    exec python manage.py run_scheduler
    ;;
  *)
    wait_for_db
    exec python manage.py "$@"
    ;;
esac
