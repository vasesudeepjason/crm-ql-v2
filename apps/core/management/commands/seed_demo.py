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
