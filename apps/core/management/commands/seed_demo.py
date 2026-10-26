"""
Seed a demo workspace (tenant 1) plus a second tenant used by the isolation tests.
Safe to re-run: it deletes and recreates the demo tenants only.

    manage.py seed_demo
    Logins (password for all: `quantum-demo-2026`):
      aisha@quantumloop.in   Owner
      vikram@quantumloop.in  Manager
      sneha@quantumloop.in   Telecaller (reports to Vikram)
      ravi@quantumloop.in    Field Executive (reports to Vikram)
"""
import random
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership, User
from apps.approvals.models import Approval
from apps.core.tenancy import rls_bypass, tenant_context
from apps.crm.models import Activity, Company, Contact, Lead
from apps.field.models import FieldSession, Visit
from apps.contracts.models import Contract, Product
from apps.invoices.models import Invoice, InvoiceLine, InvoiceSettings, Payment
from apps.tasks.models import Project, Task
from apps.telecaller.models import CallTarget
from apps.tenants.models import Plan, Tenant

PASSWORD = "quantum-demo-2026"
COMPANIES = [
    ("Sri Lakshmi Traders", "Hyderabad", "36AABCS1234A1Z5"), ("Deccan Infra Pvt Ltd", "Hyderabad", ""), ("Anand Pharma", "Secunderabad", ""),
    ("Greenfield Schools", "Hyderabad", ""), ("Metro Hospitals", "Vijayawada", ""), ("Kiran Logistics", "Hyderabad", ""),
    ("Vista Retail", "Warangal", ""), ("Nova Textiles", "Guntur", ""), ("Pinnacle Realty", "Hyderabad", ""), ("Bluewave IT Services", "Hyderabad", ""),
    ("Sunrise Dairy", "Nalgonda", ""), ("Krishna Motors", "Vijayawada", ""), ("Omega Diagnostics", "Hyderabad", ""), ("Royal Caterers", "Hyderabad", ""),
    ("Shakti Steel", "Kurnool", ""), ("Harmony Clinics", "Hyderabad", ""), ("Everest Tours", "Tirupati", ""), ("Lotus Interiors", "Hyderabad", ""),
]
FIRST = ["Arjun", "Priya", "Rahul", "Meena", "Suresh", "Divya", "Kiran", "Lakshmi", "Naveen", "Pooja", "Harish", "Swati", "Manoj", "Anita"]
LAST = ["Reddy", "Sharma", "Rao", "Naidu", "Iyer", "Verma", "Patel", "Gupta", "Kumar", "Nair"]
STAGES = ["new", "attempted", "contacted", "follow_up", "interested", "qualified", "proposal", "negotiation", "won", "lost"]
WEIGHTS = [22, 14, 16, 12, 10, 8, 6, 4, 6, 2]


class Command(BaseCommand):
    help = "Create demo tenants, users and sample CRM data."

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)
        with rls_bypass():
            demo = Tenant.objects.filter(slug__in=["quantumloop-hq", "other-co"])
            Visit.all_objects.filter(tenant__in=demo).delete()  # Visit.session is PROTECT; clear visits before the cascade
            for M in (Payment, Invoice, Contract):  # PROTECT chains: payment→invoice→company, contract→company/product
                M.all_objects.filter(tenant__in=demo).delete()
            demo.delete()
            User.objects.filter(email__endswith="@quantumloop.in").delete()
            User.objects.filter(email__endswith="@other.co").delete()
            plans = {p["code"]: Plan.objects.update_or_create(code=p["code"], defaults=p)[0] for p in [
                {"code": "starter", "name": "Starter", "price_paise_month": 2499_00, "users_included": 5, "storage_gb": 10, "modules": ["crm", "telecaller", "tasks"]},
                {"code": "growth", "name": "Growth", "price_paise_month": 5999_00, "users_included": 15, "storage_gb": 50, "modules": ["crm", "telecaller", "tasks", "field", "contracts", "invoices"]},
                {"code": "business", "name": "Business", "price_paise_month": 11999_00, "users_included": 40, "storage_gb": 200, "modules": ["crm", "telecaller", "tasks", "field", "contracts", "invoices", "hr"], "custom_roles": True, "api_access": True},
            ]}
            hq = Tenant.objects.create(name="QuantumLoop HQ", slug="quantumloop-hq", plan=plans["growth"], status=Tenant.Status.ACTIVE, gstin="36AABCQ1234A1Z5")
            other = Tenant.objects.create(name="Other Co", slug="other-co", plan=plans["starter"])

        def user(email, name, tenant, role, manager=None):
            u = User.objects.create_user(email, PASSWORD, name=name)
            m = Membership.objects.create(user=u, tenant=tenant, role=role, manager=manager)
            return u, m

        aisha, _ = user("aisha@quantumloop.in", "Aisha Rao", hq, "owner")
        vikram, m_vikram = user("vikram@quantumloop.in", "Vikram Sen", hq, "manager")
        sneha, _ = user("sneha@quantumloop.in", "Sneha Pillai", hq, "telecaller", m_vikram)
        kavya, _ = user("kavya@quantumloop.in", "Kavya Venkat", hq, "telecaller", m_vikram)
        ravi, _ = user("ravi@quantumloop.in", "Ravi Kumar", hq, "field", m_vikram)
        mohan, _ = user("mohan@quantumloop.in", "Mohan Agarwal", hq, "field", m_vikram)
        owner_pool = [vikram, sneha, kavya, ravi, mohan]
        other_owner, _ = user("owner@other.co", "Other Owner", other, "owner")

        now = timezone.now()
        with tenant_context(hq.id):
            companies = []
            for i, (name, city, gstin) in enumerate(COMPANIES):
                c = Company.objects.create(name=name, city=city, state="Telangana" if city not in ("Vijayawada", "Guntur", "Tirupati", "Kurnool") else "Andhra Pradesh",
                                           gstin=gstin, phone=f"+91 98{random.randint(10000000, 99999999)}", owner=random.choice(owner_pool), source=random.choice(["Referral", "IndiaMART", "Walk-in", "Website"]),
                                           tags=random.sample(["AMC", "Subscription", "Service contract", "Priority"], k=random.randint(0, 2)))
                c.created_by = aisha
                c.save()
                Contact.objects.create(company=c, name=f"{random.choice(FIRST)} {random.choice(LAST)}", designation=random.choice(["Owner", "Purchase Manager", "Admin Head", "Director"]), phone=f"0{random.randint(7000000000, 9999999999)}", is_primary=True)
                companies.append(c)

            leads = []
            for i in range(160):
                stage = random.choices(STAGES, WEIGHTS)[0]
                created = now - timedelta(days=random.randint(0, 62), hours=random.randint(0, 20))
                owner = random.choice(owner_pool)
                company = random.choice(companies) if random.random() < 0.7 else None
                lead = Lead(name=company.name if company else f"{random.choice(FIRST)} {random.choice(LAST)}", company=company, phone=f"+91 9{random.randint(100000000, 999999999)}",
                            stage=stage, owner=owner, source=random.choice(["Telecalling", "Referral", "IndiaMART", "JustDial", "Website"]),
                            est_value_paise=random.choice([18000, 24000, 36000, 48000, 52500, 84000, 120000, 150000]) * 100,
                            next_follow_up=(now + timedelta(days=random.randint(-4, 12))).date() if stage not in ("won", "lost") else None,
                            consent_note="Captured during outbound call; consented to follow-up", tags=[])
                if stage == "won":
                    if company is None:
                        lead.company = company = random.choice(companies)
                    lead.won_at = created + timedelta(days=random.randint(1, 12))
                    if lead.won_at > now:
                        lead.won_at = now - timedelta(hours=random.randint(1, 30))
                lead.created_by = owner
                lead.save()
                Lead.all_objects.filter(pk=lead.pk).update(created_at=created)
                leads.append(lead)
                for _ in range(random.randint(0, 4)):
                    a = Activity(lead=lead, company=company, type="call", outcome=random.choice(list(Activity.Outcome.values)), duration_seconds=random.randint(30, 600), notes="")
                    a.created_by = owner if owner in (sneha, kavya) else sneha
                    a.save()
                    Activity.all_objects.filter(pk=a.pk).update(created_at=min(created + timedelta(hours=random.randint(1, 72)), now - timedelta(minutes=random.randint(5, 600))))

            for tc, calls, leads_t in ((sneha, 40, 4), (kavya, 35, 3)):
                t = CallTarget(user=tc, calls_per_day=calls, leads_per_day=leads_t, set_by=vikram)
                t.created_by = vikram
                t.save()
            # Today's calls so the dashboard has "today" numbers
            for _ in range(37):
                a = Activity(lead=random.choice(leads), type="call", outcome=random.choice(list(Activity.Outcome.values)), duration_seconds=random.randint(30, 400))
                a.created_by = random.choice([sneha, kavya])
                a.save()
            for _ in range(5):
                l = Lead(name=f"{random.choice(FIRST)} {random.choice(LAST)}", phone=f"+91 9{random.randint(100000000, 999999999)}", stage="new", owner=sneha, source="Telecalling", est_value_paise=2400000)
                l.created_by = sneha
                l.save()

            # Projects and tasks
            today = (now + timedelta(hours=5, minutes=30)).date()  # IST
            onboarding = Project(name="Anand Pharma onboarding", description="Get the AMC contract signed, systems installed and the first invoice out.", company=companies[0], owner=vikram, status="active", color="brand", start_date=today - timedelta(days=10), due_date=today + timedelta(days=20))
            onboarding.created_by = vikram
            onboarding.save()
            onboarding.members.set([vikram, ravi, mohan, sneha])
            renewals = Project(name="Q3 renewal push", description="Call every customer whose AMC ends this quarter.", owner=vikram, status="active", color="green", start_date=today - timedelta(days=30), due_date=today + timedelta(days=5))
            renewals.created_by = vikram
            renewals.save()
            renewals.members.set([vikram, sneha, kavya])
            old = Project(name="Website leads cleanup", owner=aisha, status="done", color="grey", start_date=today - timedelta(days=60), due_date=today - timedelta(days=20))
            old.created_by = aisha
            old.save()
            old.members.set([aisha, sneha])
            plan = [
                (onboarding, "Collect signed AMC contract", ravi, -2, "high", "todo"), (onboarding, "Site survey at Anand Pharma plant", mohan, 0, "normal", "doing"),
                (onboarding, "Share installation schedule", vikram, 3, "normal", "todo"), (onboarding, "Raise first invoice", vikram, 15, "normal", "todo"),
                (onboarding, "Kick-off call with their IT head", sneha, -5, "normal", "done"),
                (renewals, "Call Nova Textiles about renewal", sneha, 0, "high", "todo"), (renewals, "Send revised quote to Krishna Motors", kavya, 1, "normal", "todo"),
                (renewals, "Chase Sri Lakshmi Traders PO", sneha, -1, "high", "doing"), (renewals, "Update renewal tracker sheet", vikram, 4, "low", "todo"),
                (renewals, "List customers expiring this quarter", kavya, -12, "normal", "done"),
                (old, "Delete duplicate website leads", sneha, -25, "normal", "done"), (old, "Re-tag sources", aisha, -22, "low", "done"),
                (None, "Book conference room for Friday review", aisha, 2, "low", "todo"), (None, "Prepare monthly sales deck", vikram, -1, "high", "todo"),
            ]
            for proj, title, who, offset, prio, st in plan:
                t = Task(title=title, project=proj, assignee=who, due_date=today + timedelta(days=offset), priority=prio, status=st, company=proj.company if proj else None)
                if st == "done":
                    t.done_at = now - timedelta(days=max(0, -offset))
                t.created_by = vikram
                t.save()

            # Catalogue and contracts
            cat = {}
            for name, kind, code, gst, price, cycle, desc in [
                ("AMC Gold", "service", "998713", 18, 4800000, "yearly", "Annual maintenance, 4 visits, parts included"),
                ("AMC Silver", "service", "998713", 18, 2400000, "yearly", "Annual maintenance, 2 visits"),
                ("CRM subscription", "service", "998314", 18, 350000, "monthly", "Per workspace, up to 10 users"),
                ("Installation service", "service", "995461", 18, 1500000, "one_time", "On-site installation and training"),
                ("Spare parts kit", "goods", "84799090", 18, 850000, "one_time", "Standard replacement kit"),
            ]:
                p = Product(name=name, kind=kind, hsn_sac=code, gst_rate=gst, default_price_paise=price, default_cycle=cycle, description=desc)
                p.created_by = aisha
                p.save()
                cat[name] = p
            owners_c = [aisha, vikram, ravi, mohan]
            ends = [-12, -3, 2, 5, 6, 11, 18, 24, 27, 33, 41, 48, 55, 59, 66, 74, 80, 88, 120, 150, 200, 260, 300, 340]
            for i, off in enumerate(ends):
                co = companies[i % len(companies)]
                prod = cat[random.choice(["AMC Gold", "AMC Silver", "AMC Gold", "CRM subscription"])]
                end = today + timedelta(days=off)
                start = end - timedelta(days=364 if prod.default_cycle != "monthly" else 364)
                contact = co.contacts.first()
                c = Contract(company=co, product=prod, start_date=start, end_date=end, billing_cycle=prod.default_cycle, amount_paise=prod.default_price_paise + random.choice([0, 0, 200000, -100000]),
                             auto_renew=(i % 7 == 0), owner=owners_c[i % 4], contact=contact, contact_email=f"accounts@{co.name.split()[0].lower()}.example.in" if i % 3 else "", notes="")
                c.created_by = vikram
                c.save()
                Contract.all_objects.filter(pk=c.pk).update(created_at=now - timedelta(days=364 - off if off < 364 else 10))
                if off <= 7:
                    c.reminders = {"owner": [90, 60, 30, 7] if off <= 7 else [90, 60, 30], "client": [30, 7] if c.contact_email else []}
                    c.save(update_fields=["reminders"])
            # a renewed chain and two lapsed ones
            first = Contract.objects.filter(end_date__gt=today + timedelta(days=100)).first()
            prev = Contract(company=first.company, product=first.product, start_date=first.start_date - timedelta(days=365), end_date=first.start_date - timedelta(days=1), billing_cycle=first.billing_cycle, amount_paise=first.amount_paise - 300000, owner=first.owner, status="renewed", decided_at=now - timedelta(days=200), decided_by=vikram)
            prev.created_by = vikram
            prev.save()
            Contract.objects.filter(pk=first.pk).update(renewal_of=prev)
            for j, (reason, days_ago) in enumerate([("Moved to a cheaper vendor", 20), ("Site closed down", 75)]):
                co = companies[(len(ends) + j) % len(companies)]
                lp = Contract(company=co, product=cat["AMC Silver"], start_date=today - timedelta(days=364 + days_ago), end_date=today - timedelta(days=days_ago), billing_cycle="yearly", amount_paise=2400000, owner=ravi, status="lapsed", lapsed_reason=reason, decided_at=now - timedelta(days=days_ago - 1), decided_by=ravi)
                lp.created_by = vikram
                lp.save()
            # tasks on contracts
            for c in Contract.objects.filter(status="active", end_date__lte=today + timedelta(days=30))[:3]:
                t = Task(title=f"Renewal call · {c.company.name}", contract=c, company=c.company, assignee=c.owner, due_date=min(c.end_date, today + timedelta(days=2)), priority="high")
                t.created_by = vikram
                t.save()

            # Invoice settings + invoices against the contracts
            InvoiceSettings.objects.update_or_create(tenant=hq, defaults=dict(legal_name="QuantumLoop Technologies Pvt Ltd", address="4th Floor, Cyber Pearl, HITEC City\nHyderabad 500081, Telangana", state_code="36", pan="AABCQ1234L", email="accounts@quantumloop.in", phone="+91 40 4012 3456", website="quantumloop.in",
                                                                               bank_account_name="QuantumLoop Technologies Pvt Ltd", bank_name="HDFC Bank, Madhapur", bank_account_no="50200012345678", bank_ifsc="HDFC0001234", upi_id="quantumloop@hdfcbank", series_prefix="QL", due_days=15))
            hq.gstin = hq.gstin or "36AABCQ1234L1ZK"
            hq.save(update_fields=["gstin"])
            inv_plan = [  # (contract index by end offset order, days ago issued, paid fraction)
                (18, 120, 1.0), (19, 95, 1.0), (20, 70, 0.5), (21, 48, 0.0), (22, 33, 1.0), (23, 20, 0.0), (0, 10, 0.0), (1, 5, 0.0),
            ]
            actives = list(Contract.objects.filter(status__in=["active", "renewed"]).order_by("-end_date"))
            for idx, ago, frac in inv_plan:
                c = actives[idx % len(actives)]
                issue_on = today - timedelta(days=ago)
                st_code = {"Telangana": "36", "Andhra Pradesh": "37"}.get(c.company.state, "36")
                inv = Invoice(company=c.company, contract=c, owner=c.owner, bill_to_name=c.company.name, bill_to_address=f"{c.company.city}, {c.company.state}", bill_to_gstin=c.company.gstin, bill_to_state=st_code, bill_to_email=c.contact_email,
                              place_of_supply=st_code, supplier_state="36", issue_date=issue_on, due_date=issue_on + timedelta(days=15), period_start=c.start_date, period_end=c.end_date, status="issued", terms="Payment due within 15 days.\nInterest at 18% p.a. on overdue amounts.")
                inv.created_by = aisha
                inv.save()
                InvoiceLine(invoice=inv, product=c.product, description=f"{c.display_title} · {c.start_date:%d %b %Y} to {c.end_date:%d %b %Y}", hsn_sac=c.product.hsn_sac, rate_paise=c.amount_paise, gst_rate=c.product.gst_rate).save()
                inv.assign_number("QL")
                inv.issued_at = now - timedelta(days=ago)
                inv.save()
                inv.recompute()
                if frac:
                    p = Payment(invoice=inv, amount_paise=int(inv.total_paise * frac), mode=random.choice(["upi", "bank", "cheque"]), reference=f"UTR{random.randint(100000, 999999)}", paid_on=issue_on + timedelta(days=random.randint(2, 14)))
                    p.created_by = aisha
                    p.save()
                    inv.recompute()
            d = Invoice(company=companies[3], owner=vikram, bill_to_name=companies[3].name, bill_to_address=f"{companies[3].city}, {companies[3].state}", bill_to_gstin=companies[3].gstin, bill_to_state="36", place_of_supply="36", supplier_state="36", issue_date=today, due_date=today + timedelta(days=15), status="draft")
            d.created_by = vikram
            d.save()
            InvoiceLine(invoice=d, product=cat["Installation service"], description="Installation service", hsn_sac="995461", rate_paise=1500000, gst_rate=18).save()
            d.recompute()

            # Pending approvals: telecaller leads + field visits
            ct_lead = ContentType.objects.get_for_model(Lead)
            samples = [
                (ravi, "visit", "Sri Lakshmi Traders", 8400000, 2, {"outcome": "Interested · AMC renewal", "location": "17.4401, 78.3489 · ±8 m", "duration": "34 min", "note": "Met the purchase manager. They want the AMC renewed for two years with quarterly billing. Asked for a revised quote before Friday."}),
                (sneha, "lead", "Deccan Infra Pvt Ltd", None, 0, {"outcome": "Interested", "note": "Spoke to the admin head; wants a demo next week."}),
                (mohan, "visit", "Anand Pharma", 5250000, 1, {"outcome": "Proposal discussed", "location": "17.4399, 78.4983 · ±12 m", "duration": "22 min", "note": "Renewal discussion, decision by month end."}),
                (kavya, "lead", "Greenfield Schools", None, 3, {"outcome": "Call back", "note": "Principal asked for brochure on WhatsApp."}),
                (ravi, "visit", "Metro Hospitals", 12000000, 0, {"outcome": "Proposal sent", "location": "16.5062, 80.6480 · ±6 m", "duration": "48 min", "note": "Walked through the AMC proposal with the facilities head."}),
                (mohan, "visit", "Kiran Logistics", 5250000, 4, {"outcome": "Interested", "location": "17.3850, 78.4867 · ±15 m", "duration": "18 min", "note": "Wants support contract for 2 depots."}),
                (kavya, "lead", "Nova Textiles", None, 1, {"outcome": "Interested", "note": "New lead from IndiaMART enquiry."}),
            ]
            ct_visit = ContentType.objects.get_for_model(Visit)
            outcome_for = {"Interested · AMC renewal": "interested", "Proposal discussed": "follow_up", "Proposal sent": "follow_up", "Interested": "interested"}
            for submitter, kind, co, value, days_ago, summary in samples:
                comp = next((c for c in companies if c.name == co), None)
                when = now - timedelta(days=days_ago, hours=random.randint(1, 6))
                if kind == "lead":
                    lead = Lead(name=co, company=comp, phone=f"+91 9{random.randint(100000000, 999999999)}", stage="new", status=Lead.Status.PENDING, owner=submitter, source="Telecalling", est_value_paise=value, consent_note="Captured during call; consent noted")
                    lead.created_by = submitter
                    lead.save()
                    a = Approval(kind=kind, content_type=ct_lead, object_id=lead.id, submitter=submitter, title=co, company_name=co, value_paise=value, summary=summary, submitted_at=when)
                else:
                    lat, lng = [float(x) for x in summary["location"].split(" · ")[0].split(", ")]
                    sess = FieldSession(executive=submitter, started_at=when - timedelta(hours=3), start_lat=lat, start_lng=lng, ended_at=when + timedelta(hours=4), end_source="manual")
                    sess.created_by = submitter
                    sess.save()
                    lead = Lead.objects.filter(company=comp, owner=submitter).exclude(stage__in=["won", "lost"]).first()
                    visit = Visit(session=sess, executive=submitter, company=comp, lead=lead, contact_name="Purchase manager", lat=lat, lng=lng, accuracy=float(summary["location"].split("±")[1].split(" ")[0]),
                                  captured_at=when, duration_minutes=int(summary["duration"].split(" ")[0]), outcome=outcome_for.get(summary["outcome"], "other"), notes=summary["note"], next_follow_up=(when + timedelta(days=7)).date())
                    visit.created_by = submitter
                    visit.save()
                    a = Approval(kind=kind, content_type=ct_visit, object_id=visit.id, submitter=submitter, title=co, company_name=co, value_paise=value, summary={**summary, "has_photo": False, "accuracy": visit.accuracy, "low_accuracy": visit.low_accuracy, "mock": False}, submitted_at=when)
                a.created_by = submitter
                a.save()
            # some decided ones for the other tabs
            for i in range(12):
                lead = Lead(name=random.choice(companies).name, stage="contacted", owner=sneha, source="Telecalling")
                lead.created_by = sneha
                lead.save()
                a = Approval(kind="lead", content_type=ct_lead, object_id=lead.id, submitter=sneha, reviewer=vikram, title=lead.name, company_name=lead.name,
                             status="approved" if i < 9 else "rejected", reason="" if i < 9 else "Duplicate of an existing lead", submitted_at=now - timedelta(days=random.randint(3, 20)), decided_at=now - timedelta(days=random.randint(1, 2)))
                a.created_by = sneha
                a.save()

        with tenant_context(other.id):
            c = Company(name="Other Co Customer", city="Pune", owner=other_owner)
            c.created_by = other_owner
            c.save()
            l = Lead(name="Other Co Lead", company=c, stage="new", owner=other_owner, est_value_paise=100000)
            l.created_by = other_owner
            l.save()

        self.stdout.write(self.style.SUCCESS(f"Seeded tenant '{hq}' (id {hq.id}) and '{other}' (id {other.id}). Login aisha@quantumloop.in / {PASSWORD}"))
