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

Sign-in options: email + password, or "Email me a sign-in code" (6-digit code, 10 minutes, printed to the console in development). Owners and admins can turn on two-step verification (TOTP) under **Set up → Profile & security**; the seed is stored encrypted with `FIELD_ENCRYPTION_KEY`. Login and code requests are rate-limited.

Invite colleagues from **Set up → Users & roles** (role, optional manager). The invite email carries a single-use link valid for 7 days; plan seat limits are enforced. Role changes, deactivations, invites and sign-ins are all written to the audit log.

**Import wizard** (Set up → Import wizard): upload CSV/XLSX of companies or leads, pick the source (Excel, Zoho/Bigin, Tally, IndiaMART/JustDial, Freshsales/LeadSquared/HubSpot) so columns map themselves, confirm the mapping (saved for next time) and the pipeline-stage table, then dry run → commit → roll back within 14 days. Phones are normalised, GSTIN/email/dates validated, duplicates flagged within the file and against existing records (suggested, never merged). A needs-fixing CSV lists every rejected row with its reason. Rows keep source, original ID and raw values; a re-import with the same Original ID updates instead of duplicating.

**Audit log** (Set up → Audit log): filter by group, person, period and text; click a row for before/after and IP.

**Workspace** (Set up → Workspace): name, timezone, GSTIN, address; the list of support sessions with "End access"; DPDP export of all data as a zip of CSVs; deletion request (Owner only, confirmed by QuantumLoop before anything is removed).

Platform staff (`is_staff`): `python manage.py createsuperuser`, then http://localhost:8000/platform/support/. Staff enter a customer workspace only through a **support session** — a reason and a time limit (5 min to 4 h) — during which they act as an Admin and every action is audited with the session id. The customer's admins see the session on their Workspace page and can end it; sessions expire on their own. The Django admin at /platform/ remains for tenants, plans and users.

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
apps/accounts     User (email login, TOTP), Membership (one role per tenant), fixed roles → permissions,
                  OneTimeCode (email sign-in), Invitation, settings screens (profile, users & roles)
apps/crm          Company, Contact, Lead (+stage history), Activity (calls, notes, tasks)
apps/imports      Import wizard: ImportJob/ImportRow/SavedMapping, source templates, parsers, validate/commit/rollback
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
