# Deploying QuantumLoop ERP

One VPS in an India region (section 9 of the foundation document) runs everything with
Docker Compose: Postgres 16, Redis 7, the app image three times (web, worker, scheduler)
and Caddy for TLS. Sizing for the first customers: 2 vCPU, 4 GB RAM, 40 GB SSD.

## What the platform guarantees, and what makes it true

| Guarantee | Where it lives | How it can silently break |
|---|---|---|
| Tenant A cannot read tenant B, even with raw SQL | Postgres row-level security, `apps/core/rls.py`, policy re-applied by the `core` migrations | Connecting as a **superuser** or a role with **BYPASSRLS**. `manage.py check` errors outside DEBUG; CI runs the suite as the plain `quantum_app` role. |
| Bank details, IDs, salaries, TOTP seeds unreadable from a DB dump | Fernet with `FIELD_ENCRYPTION_KEY`, `apps/core/crypto.py` | Losing the key (data is gone) or leaking it (data is readable). Keep it in the secret store **and** an offline copy. Never rotate without re-encrypting. |
| Visit photos and employee documents are private | Served through permission-checked views; Caddy never exposes `/media` | Adding a static file server for `media/`. Don't. |
| Reminders, digests, renewals run every morning | `scheduler` container → `manage.py daily_jobs` at 07:30 IST | Scheduler container stopped. Check `docker compose ps`. |
| Emails and exports do not block requests | `worker` container (RQ) with `RQ_ENABLED=1` | Redis down → jobs run inline (slower, not lost). |

## First deployment

1. **Server**: Ubuntu 24.04, Docker Engine + Compose plugin, a DNS A record for `app.<domain>` pointing at it, ports 80 and 443 open. Create a non-root user in the `docker` group.
2. **Clone** the repository into `/srv/quantumloop` and `cp .env.production.example .env.production`.
3. **Secrets** in `.env.production`:
   - `DJANGO_SECRET_KEY`: `python3 -c "import secrets;print(secrets.token_urlsafe(64))"`
   - `FIELD_ENCRYPTION_KEY`: `python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` — **store a copy outside the server** (password manager / sealed envelope).
   - `POSTGRES_SUPERUSER_PASSWORD` and `APP_DB_PASSWORD`: two different long random strings.
   - `SITE_DOMAIN`, `SITE_URL`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` for your domain.
   - SMTP settings for transactional mail (ZeptoMail, SES Mumbai or Postmark). Set up SPF and DKIM for the sending domain first or invites land in spam.
4. **Start**: `docker compose -f docker-compose.prod.yml up -d --build`. The `web` container waits for the database, runs migrations, runs `check --deploy` (it refuses to start on a superuser role, a dev secret key or a missing encryption key) and then serves.
5. **Platform admin**: `docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser`. Sign in at `/platform/`, create the first tenant and plan, invite its Owner. Support staff enter customer workspaces only through `/platform/support/` sessions.
6. **Smoke test**: `https://app.<domain>/healthz` returns `{"ok": true, "db": "postgresql"}`; sign in; create a lead; upload a visit photo; raise an invoice and download the PDF; check the bell and an email.
7. **Backups** (daily, before you let a customer in):
   - Database: `docker compose -f docker-compose.prod.yml exec -T db pg_dump -U quantum -Fc quantumloop > /backups/ql-$(date +%F).dump`, kept 30 days, copied off the server (object storage in an India region). Restore with `pg_restore -d quantumloop --clean`.
   - Media volume (`/var/lib/docker/volumes/<project>_media`): nightly rsync or snapshot.
   - Test a restore once a quarter; the restore plan is part of every migration review.
8. **Monitoring**: uptime check on `/healthz` (1 min), disk and memory alerts, error tracking (Sentry; set `SENTRY_DSN` when added), log shipping of `docker compose logs`.

## Every release

```
git pull
docker compose -f docker-compose.prod.yml build web
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f web   # watch migrations and the deploy check
```

Migrations run automatically on `web` start. The old container keeps serving until the new one is healthy. **Rollback** = `git checkout <previous tag>` and the same three commands; reverse migrations are written for every schema change so `manage.py migrate <app> <previous>` is available if a release must be undone after data has been written.

CI (`.github/workflows/ci.yml`) must be green before a release: tests on Postgres with RLS enforced, migrations committed, dependency scan, image builds. Protect `main` so that is a rule, not a habit.

## Staging

A second compose project on the same or a smaller box with its own `.env.staging` (different domain, separate database and volumes). It holds the sandbox tenant with `manage.py seed_demo` data only — never real customer data. Deploy there first, run the smoke test, then promote the same image to production.

## Operations cheat sheet

| Task | Command |
|---|---|
| Daily jobs now | `exec scheduler python manage.py daily_jobs` |
| Re-apply RLS after adding a model | `makemigrations` + a `core` migration with `apply_rls`, as in `0010_rls_hr.py` |
| Rotate the Django secret key | change the env var, restart; everyone signs in again |
| Export a tenant (DPDP) | Owner → Set up → Workspace → Export all data |
| Delete a tenant | request on the Workspace page → platform admin deletes after confirming with the Owner |
| Who touched what | Set up → Audit log (append-only) |

## Still to decide before customers

- Hosting provider and region (AWS Mumbai, DigitalOcean Bangalore, or an Indian provider) and the sub-processor list for the privacy page.
- Email provider and the sending domain.
- Error tracking account.
- Who holds the `FIELD_ENCRYPTION_KEY` offline copy, and who may run `/platform/support/`.
- A lawyer and a CA to read the DPDP and GST points in the foundation document before terms are published.
