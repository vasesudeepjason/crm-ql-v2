# QuantumLoop ERP

Multi-tenant SaaS for Indian small businesses: leads, telecaller and field-sales approval, contracts and renewals, invoices, HR. Spec: `QuantumLoop ERP - Foundation Document v1.pdf`.

**Stack:** Django 5 · Django templates + HTMX + Alpine.js (React islands only where interactivity earns it) · PostgreSQL with row-level security · Redis + Django-RQ · PWA for field staff.

## Run locally

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows; use source .venv/bin/activate elsewhere
pip install -r requirements.txt
copy .env.example .env                               # edit DATABASE_URL if needed
docker compose up -d db redis                        # PostgreSQL + Redis
python manage.py migrate
python manage.py seed_demo                           # demo workspace + users
python manage.py runserver
```

Open http://localhost:8000 and sign in as `aisha@quantumloop.in` / `quantum-demo-2026` (Owner). Other demo users: `vikram@` (Manager), `sneha@` / `kavya@` (Telecallers), `ravi@` / `mohan@` (Field Executives) — same domain and password.

Without Docker, leave `DATABASE_URL` unset and the app runs on SQLite. Row-level security and its test only exist on PostgreSQL, so use Postgres for anything beyond a first look.

Platform admin (QuantumLoop staff): `python manage.py createsuperuser`, then http://localhost:8000/platform/.

## Tests

```bash
pytest
```

CI runs `manage.py check`, `makemigrations --check` and the test suite against PostgreSQL on every push and pull request.

## Layout

```
quantumloop/      settings, urls
apps/core         TenantModel base, tenancy middleware, RLS, audit log, permissions, phone/GSTIN/money helpers
apps/tenants      Tenant, Plan
apps/accounts     User (email login), Membership (one role per tenant), fixed roles → permissions
apps/crm          Company, Contact, Lead (+stage history), Activity (calls, notes, tasks)
apps/approvals    One Approval record for field visits and telecaller leads; approve / reject / ask-for-info rules
apps/dashboard    Home dashboard
templates/        base layout, partials, pages
static/           css/quantum.css (design system), js, logo
design/           v1 and v2 HTML design references (not served by the app)
tests/            tenant isolation, permissions, approval rules, helpers
```

## How tenancy works

1. `TenantMiddleware` resolves the tenant from the signed-in user's `Membership` and stores it in a context variable.
2. `TenantModel.objects` filters every query on that tenant; `all_objects` is the explicit escape hatch.
3. On PostgreSQL the middleware also sets `app.tenant_id` on the connection, and every tenant table has a row-level-security policy (`apps/core/rls.py`). The database refuses rows from any other tenant even for raw SQL. Platform admin screens set `app.bypass_rls`, which is written to the audit log.

New tenant table? Subclass `TenantModel`, run `makemigrations`, then `python manage.py apply_rls` (or add a migration calling `apply_rls`).

## Definition of done (foundation document, section 6)

- No secrets in the repo; env vars only.
- Every change via pull request; CI must pass.
- Business rules (approvals, permissions, tenancy) have tests; every endpoint has a cross-tenant test.
- Schema changes ship only as migrations.
