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

Open http://localhost:8010 and sign in as `aisha@quantumloop.in` / `quantum-demo-2026` (Owner). Other demo users: `vikram@` (Manager), `sneha@` / `kavya@` (Telecallers), `ravi@` / `mohan@` (Field Executives) — same domain and password.

Without Docker, leave `DATABASE_URL` unset and the app runs on SQLite. Row-level security and its test only exist on PostgreSQL, so use Postgres for anything beyond a first look.

Sign-in options: email + password, or "Email me a sign-in code" (6-digit code, 10 minutes, printed to the console in development). Owners and admins can turn on two-step verification (TOTP) under **Set up → Profile & security**; the seed is stored encrypted with `FIELD_ENCRYPTION_KEY`. Login and code requests are rate-limited.

Invite colleagues from **Set up → Users & roles** (role, optional manager). The invite email carries a single-use link valid for 7 days; plan seat limits are enforced. Role changes, deactivations, invites and sign-ins are all written to the audit log.

**Import wizard** (Set up → Import wizard): upload CSV/XLSX of companies or leads, pick the source (Excel, Zoho/Bigin, Tally, IndiaMART/JustDial, Freshsales/LeadSquared/HubSpot) so columns map themselves, confirm the mapping (saved for next time) and the pipeline-stage table, then dry run → commit → roll back within 14 days. Phones are normalised, GSTIN/email/dates validated, duplicates flagged within the file and against existing records (suggested, never merged). A needs-fixing CSV lists every rejected row with its reason. Rows keep source, original ID and raw values; a re-import with the same Original ID updates instead of duplicating.

**Audit log** (Set up → Audit log): filter by group, person, period and text; click a row for before/after and IP.

**Leads** (Phase 1): create/edit with owner assignment by scope and an inline new company; a live duplicate check on phone, email and similar name that suggests matches and never merges (tick "Create anyway" to override, which is audited); a detail page with calls, notes, tasks, stage history and approval events on one timeline; stage changes with the rules (Won needs a company, Lost needs a reason); and a drag-and-drop pipeline board at Leads → Pipeline (tap-to-move on touch). Telecaller-created leads go to the approval queue as "Pending approval".

**Field sales** (Phase 1, part 1): a field executive starts their day (location is recorded only while the day is active and the app says so), records visits with a photo (compressed on the device, shrunk again on the server, served only through a permission-checked URL), GPS position and accuracy, outcome, notes and next follow-up; each visit goes to the shared approval queue, and approved visits post a meeting activity on the lead, move its follow-up date and advance its stage. Days auto-close after 21:00 local or on the next morning (`manage.py close_sessions` nightly). Managers get Field sales → Team (who is on the road, today's counts, 7/30/90-day performance, average review time, low-accuracy counts, end someone's day) and a review queue with kind / executive / date filters and bulk approve. **Part 2:** the app installs to the home screen (manifest + service worker at `/sw.js`, offline fallback page); visits recorded without a network are kept on the phone in IndexedDB with their photo and sent automatically when the connection returns (also via Background Sync), deduplicated by `offline_id`, keeping the real capture time and attaching to the day that was running.

**Telecaller** (Phase 1): Telecaller → Call desk shows today's calls and leads against the daily target, a "next to call" queue (overdue and due follow-ups first, then new leads nobody has called, do-not-contact and pending leads excluded), one-click tel: links and a drawer to log the call (outcome, duration, notes, next follow-up, optional stage move; no answer/busy sets tomorrow, wrong number flags do-not-contact, interested/not-interested move the stage). "Add lead from call" prefills the lead form and logs the first call; telecaller leads go to the approval queue. Managers get Telecaller → Team & targets: today's progress, 1/7/30-day calls, connect rate, talk time, leads submitted/approved/rejected, target attainment, and inline target editing (audited). The dashboard shows a telecaller's targets bar.

**Reports** (Phase 1, Reports in the sidebar; Manager and above, scoped to what you may see): four tabs over a period (7/30/90 days, this financial year from 1 April, or custom dates) and an optional owner filter. *Pipeline*: open leads, value, overdue and missing follow-ups, stale leads, by stage, by source, by owner. *Conversion*: a cohort funnel of leads created in the period and how far they got, win rate, average days to win, conversion by source, lost reasons. *Team*: per person leads, wins, won value, calls, talk time, visits, approvals submitted/approved/rejected, reviews done, open and overdue. *Activity*: daily leads, calls, visits and wins. **Exports** (anyone with `data.export`: Owner, Admin, Accountant): leads, companies, calls, visits and approvals as CSV with the same scope and filters, each download audited; Leads also has an Export button.

**Notifications** (Phase 1): the bell lists what concerns you and marks items read when opened. A submitted lead or visit notifies the submitter's manager (or the owners/admins when there is none); a decision or a request for more information notifies the submitter. Each morning `manage.py daily_jobs` (cron 07:30 IST, or rq-scheduler) closes stale field days, sends every owner a digest of follow-ups due today and overdue, every assignee a digest of tasks due, tells managers about approvals waiting more than two working days, rolls auto-renew contracts over and sends renewal reminders. Emails are plain text and follow the per-user switches on Profile → Email notifications (in-app notifications are always on). With `RQ_ENABLED=1` and `REDIS_URL` set, emails and digests run on the background worker:

```bash
python manage.py rqworker default
```

Without Redis they run inline, so development and tests need nothing extra. The queue dashboard for staff is at /platform/rq/.

**Tasks & projects** (Phase 1): *My tasks* groups what is assigned to you into Overdue / Today / This week / Later / No date, with a one-line quick add (title, due date, who), a tick to finish or reopen, and a drawer for the full task (details, assignee, priority, status, project, company). Managers switch to Everyone, Unassigned or one person; visibility follows `tasks.view` scope (own, team, all) plus project membership. "Add task" on a lead creates a task linked to that lead (and its company), shown in the lead's timeline and Open tasks. *Projects* (Manager and above create; members see them) group tasks for a customer onboarding or a renewal push: owner, members, company, dates, colour, progress; each has a To do / In progress / Done board with drag-and-drop (tap-to-move on touch). Assigning a task to someone else notifies them (in-app, email per preference); the morning digest lists tasks due today and overdue; the sidebar shows your count of tasks due. Linking tasks to contracts arrives with Phase 2.

**Contracts & renewals** (Phase 2, part 1; Contracts in the sidebar): a **catalogue** of products and services with HSN/SAC code, GST rate, default price and usual billing cycle (Managers, Accountants and Admins maintain it). **One contract per purchase**: company, catalogue item, start and end, billing cycle, amount per cycle before GST (GST shown from the item's rate), auto-renew flag, renewal owner, client contact and email, notes; numbered `CT/26-27/0001` per financial year. A Won lead with a company shows a "Create contract" banner that prefills from the lead. The **renewal tracker** buckets active contracts into expired, 7, 30, 60 and 90 days with the annualised revenue at risk for each, plus lapsed contracts for win-back and what is coming in 3 to 6 months; the dashboard and sidebar surface what ends within 30 days. **Reminders** run with the morning jobs: the owner at 90, 60, 30 and 7 days (in-app, email per preference), the client email at 30 and 7, each threshold once even if a day is missed. The owner marks a renewal **renewed** (a new linked contract for the next term, the invoice follows in part 2) or **lapsed** with a reason; a lapsed contract can be won back later. Auto-renew contracts roll into a new term of the same length the day after expiry. Tasks can be linked to a contract; contracts export as CSV; visibility follows `contracts.view` scope (Sales Executives own theirs, Managers their team's, Accountants all).

**Invoices & payments** (Phase 2, part 2; Invoices in the sidebar). **Invoice settings** on the Workspace page: legal name, address, supplier state, PAN, bank and UPI details, logo, number series prefix, credit days, default terms. **Invoices** start as drafts, from a contract (amount, period, HSN/SAC and GST rate come across; the contract page offers "Raise invoice") or blank for any company; lines pick from the catalogue or are typed freely with their own GST rate; the buyer's GSTIN, address and state are snapshotted so the invoice reads the same later. Place of supply versus supplier state decides **CGST+SGST or IGST** automatically; tax is computed per line in paise, summarised per rate, and the total is rounded to the rupee with the round-off shown. **Issuing** assigns the next number in the series for the financial year (`QL/26-27/0001`, restarting each April) and locks the invoice; cancelling keeps the number used. The **PDF** (the layout from the previous CRM, now driven by the model) downloads or is **emailed** to the client with one click. **Payments** by UPI, bank transfer, cash, cheque or card, part payments allowed; the invoice closes itself when the balance reaches zero. **Outstanding & ageing** buckets every customer's balance into not due, 1–30, 31–60, 61–90 and over 90 days with recent payments; the dashboard and sidebar show what is overdue. **Accounting exports**: Zoho Books invoice CSV and Tally sales-voucher XML (ledgers named "Sales @18%", "Output CGST @9%" etc. must exist in Tally), plus plain CSVs of invoices, payments and ageing. Visibility follows `invoices.view` (Sales Executives own theirs, Managers their team's, Accountants all); payments need `payments.create` (Accountant, Admin, Owner). No ledger, GST return or e-invoicing (foundation document scope).

**HR** (Phase 3; HR in the sidebar). **Employee master** separate from the login (staff without computer access are still recorded; link a login when they use the app): profile, department, designation, reporting manager, employment type, dates, emergency contact, photo, notes. **Bank details, PAN, Aadhaar, UAN and salary are encrypted per field** and shown only to HR and the Owner; Aadhaar is masked on screen; every change is audited. **Documents** (ID, address proof, letters, contracts, certificates) are stored privately and served only to HR and the employee, with expiry dates. **Onboarding and exit checklists** tick off per employee from templates in HR settings; **offer and appointment letters** print from editable templates with placeholders. **Attendance**: everyone with a linked record checks in and out from My HR or the dashboard; field executives are posted automatically from their field day; HR marks present, half day, work-from-home or absent for anyone, sees today's board and a month register (employee × day with P/H/W/L/A/WO/HO) and exports a payroll CSV with payable days. **Leave**: types with days per financial year (paid or unpaid), balances with HR adjustments, requests from My HR (week-offs and holidays are not counted, overlaps and over-balance refused), approval by the reporting manager or HR with notifications both ways, approved leave posts to attendance, cancellations restore the balance, HR can record leave on someone's behalf. **Holiday calendar** per workspace with optional holidays. Settings: working hours, half-day threshold, week-off days, checklists, letter templates, leave types, holidays, departments. Payroll, PF, ESI and TDS are out of scope by design.

**Workspace** (Set up → Workspace): name, timezone, GSTIN, address; the list of support sessions with "End access"; DPDP export of all data as a zip of CSVs; deletion request (Owner only, confirmed by QuantumLoop before anything is removed).

Platform staff (`is_staff`): `python manage.py createsuperuser`, then http://localhost:8010/platform/support/. Staff enter a customer workspace only through a **support session** — a reason and a time limit (5 min to 4 h) — during which they act as an Admin and every action is audited with the session id. The customer's admins see the session on their Workspace page and can end it; sessions expire on their own. The Django admin at /platform/ remains for tenants, plans and users.

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
apps/field        FieldSession, Visit, capture form, My day, Team page, close_sessions command
apps/telecaller   CallTarget, call desk + queue, quick-log drawer, team & targets page
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
