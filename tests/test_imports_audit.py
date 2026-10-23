import io
import zoneinfo
from datetime import date

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.tenancy import rls_bypass, tenant_context
from apps.crm.models import Activity, Company, Contact, Lead
from apps.imports import services
from apps.imports.models import ImportJob, ImportRow, SavedMapping
from apps.imports.parsers import ParseError, parse
from apps.imports.specs import auto_map, guess_stage

COMPANIES_CSV = """Company,GSTIN,Phone,Email,City,Owner,Contact person,Created on,ID
Sri Lakshmi Traders,27AAPFU0939F1ZV,98480 12345,accounts@srilakshmi.in,Hyderabad,tc@a.test,Ramesh Rao,01/04/2025,C-1
Deccan Infra,,040-2345 6789,,Hyderabad,nobody@x.test,,2025-05-02,C-2
Bad Phone Co,,1,,Warangal,,,,C-3
Bad GST Co,27AAPFU0939F1ZW,98480 99999,,Guntur,,,,C-4
Sri Lakshmi Traders,,98480 12345,,Hyderabad,,,,C-5
A Customer,,,,Pune,,,,C-6
"""


def _upload(client, content: str, entity="companies", source="generic", filename="data.csv"):
    f = SimpleUploadedFile(filename, content.encode("utf-8"), content_type="text/csv")
    return client.post(reverse("imports:new"), {"entity": entity, "source": source, "file": f})


# ---------- parsing & auto-map ----------

def test_parse_csv_and_xlsx_and_limits():
    cols, rows = parse("x.csv", "Name;Phone\nA;1\n\n B ; 2 \n".encode())
    assert cols == ["Name", "Phone"] and rows == [{"Name": "A", "Phone": "1"}, {"Name": "B", "Phone": "2"}]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Lead name", "Created on", "Value"])
    ws.append(["Priya", date(2026, 1, 5), 48000.0])
    buf = io.BytesIO()
    wb.save(buf)
    cols, rows = parse("x.xlsx", buf.getvalue())
    assert cols == ["Lead name", "Created on", "Value"] and rows[0] == {"Lead name": "Priya", "Created on": "2026-01-05", "Value": "48000"}
    with pytest.raises(ParseError):
        parse("x.pdf", b"")
    with pytest.raises(ParseError):
        parse("x.csv", b"")


def test_auto_map_zoho_leads_joins_name_parts_and_guesses_stages():
    m = auto_map("leads", "zoho", ["Lead Id", "First Name", "Last Name", "Company", "Mobile", "Lead Status", "Lead Owner", "Created Time", "Random"])
    assert m == {"Lead Id": "external_id", "First Name": "name", "Last Name": "name", "Company": "company_name", "Mobile": "phone", "Lead Status": "stage", "Lead Owner": "owner_email", "Created Time": "created_at"}
    assert guess_stage("Closed Won") == "won" and guess_stage("Proposal/Price Quote") == "proposal" and guess_stage("Whatever") == "new"


# ---------- the wizard end to end ----------

def test_companies_dry_run_commit_and_rollback(two_tenants, login, client):
    login(two_tenants["owner_a"])
    r = _upload(client, COMPANIES_CSV)
    job = ImportJob.all_objects.get()
    assert r.status_code == 302 and r.url == reverse("imports:map", args=[job.id])
    assert job.row_count == 6 and job.mapping["Company"] == "name" and job.mapping["Owner"] == "owner_email"

    # map (accept the suggestion) + save mapping → validates
    post = {f"col:{c}": f for c, f in job.mapping.items()}
    post.update({"save_mapping": "1", "mapping_name": "My Excel"})
    r = client.post(reverse("imports:map", args=[job.id]), post)
    assert r.status_code == 302
    job.refresh_from_db()
    assert job.status == "validated"
    assert job.counts == {"new": 2, "update": 0, "skip": 2, "error": 2}
    with tenant_context(two_tenants["a"].id):
        rows = {r.row_no: r for r in ImportRow.objects.filter(job=job)}
    assert rows[2].status == "new" and rows[2].mapped["phone_normalized"] == "+919848012345" and rows[2].mapped["created_at"] == "2025-04-01"
    assert rows[3].status == "new" and "nobody@x.test" in rows[3].messages[0]  # unknown owner → note, not error
    assert rows[4].status == "error" and "not a valid number" in rows[4].messages[0]
    assert rows[5].status == "error" and "checksum" in rows[5].messages[0]
    assert rows[6].status == "skip" and "row 2" in rows[6].messages[0]  # duplicate phone within the file
    assert rows[7].status == "skip" and rows[7].duplicate_of == "A Customer"  # existing record, never merged
    assert SavedMapping.all_objects.get().name == "My Excel"

    # the needs-fixing file carries original columns + problems
    r = client.get(reverse("imports:errors", args=[job.id]))
    body = r.content.decode()
    assert "Bad Phone Co" in body and "Problems" in body and "Sri Lakshmi Traders" in body

    # commit
    r = client.post(reverse("imports:commit", args=[job.id]))
    assert r.status_code == 302
    job.refresh_from_db()
    assert job.status == "committed" and job.counts["created"] == 2 and job.can_rollback
    with tenant_context(two_tenants["a"].id):
        c = Company.objects.get(name="Sri Lakshmi Traders")
        assert c.owner == two_tenants["tc_a"] and c.gstin == "27AAPFU0939F1ZV" and c.external_id == "C-1" and c.source_system == "generic"
        assert timezone.localtime(c.created_at, zoneinfo.ZoneInfo("Asia/Kolkata")).date() == date(2025, 4, 1)  # midnight in the tenant's zone
        assert Contact.objects.get(company=c).name == "Ramesh Rao"
        d = Company.objects.get(name="Deccan Infra")
        assert d.owner == two_tenants["owner_a"]  # unknown owner → importer
        assert Company.objects.count() == 3  # 1 existing + 2 imported
        assert AuditLog.objects.filter(action="import.commit").exists()

    # rollback
    r = client.post(reverse("imports:rollback", args=[job.id]))
    assert r.status_code == 302
    job.refresh_from_db()
    assert job.status == "rolled_back"
    with tenant_context(two_tenants["a"].id):
        assert Company.objects.count() == 1 and Contact.objects.count() == 0
        assert Company.all_objects.filter(is_deleted=True).count() == 2
    assert client.post(reverse("imports:rollback", args=[job.id])).status_code == 302  # second rollback refused politely


def test_reimport_with_original_id_updates_instead_of_duplicating(two_tenants, login, client):
    login(two_tenants["owner_a"])
    csv1 = "Company,Phone,City,ID\nNova Textiles,98111 22222,Guntur,Z-9\n"
    _upload(client, csv1, source="zoho")
    job = ImportJob.all_objects.latest("id")
    client.post(reverse("imports:map", args=[job.id]), {f"col:{c}": f for c, f in job.mapping.items()})
    client.post(reverse("imports:commit", args=[job.id]))
    with tenant_context(two_tenants["a"].id):
        nova = Company.objects.get(name="Nova Textiles")
        assert nova.city == "Guntur"

    csv2 = "Company,Phone,City,ID\nNova Textiles Ltd,98111 22222,Vijayawada,Z-9\n"
    _upload(client, csv2, source="zoho")
    job2 = ImportJob.all_objects.latest("id")
    assert job2.mapping == job.mapping  # saved? no — auto-mapped identically; fine either way
    client.post(reverse("imports:map", args=[job2.id]), {f"col:{c}": f for c, f in job2.mapping.items()})
    job2.refresh_from_db()
    assert job2.counts["update"] == 1 and job2.counts["new"] == 0
    client.post(reverse("imports:commit", args=[job2.id]))
    with tenant_context(two_tenants["a"].id):
        assert Company.objects.count() == 2  # existing A Customer + Nova, no duplicate
        nova.refresh_from_db()
        assert nova.name == "Nova Textiles Ltd" and nova.city == "Vijayawada"
    # rolling back the update restores the previous values
    client.post(reverse("imports:rollback", args=[job2.id]))
    with tenant_context(two_tenants["a"].id):
        nova.refresh_from_db()
        assert nova.name == "Nova Textiles" and nova.city == "Guntur" and not nova.is_deleted


def test_leads_import_with_stage_map_company_creation_and_notes(two_tenants, login, client):
    login(two_tenants["owner_a"])
    csv = "Lead name,Company,Mobile,Lead Status,Value,Notes,Next follow up\nPriya Sharma,Omega Diagnostics,9876543210,Hot,₹1.2L,Wants demo,15/11/2026\nRahul Verma,A Customer,9876500000,Closed Won,48000,,\n"
    _upload(client, csv, entity="leads", source="zoho")
    job = ImportJob.all_objects.latest("id")
    assert job.mapping["Lead Status"] == "stage"
    post = {f"col:{c}": f for c, f in job.mapping.items()}
    post.update({"stage:Hot": "interested", "stage:Closed Won": "won"})
    client.post(reverse("imports:map", args=[job.id]), post)
    job.refresh_from_db()
    assert job.stage_map == {"Hot": "interested", "Closed Won": "won"} and job.counts["new"] == 2
    client.post(reverse("imports:commit", args=[job.id]))
    with tenant_context(two_tenants["a"].id):
        priya = Lead.objects.get(name="Priya Sharma")
        assert priya.stage == "interested" and priya.est_value_paise == 120000_00 and priya.next_follow_up == date(2026, 11, 15)
        assert priya.company.name == "Omega Diagnostics" and priya.company.source_system == "zoho"  # created on the fly
        assert Activity.objects.get(lead=priya).notes == "Wants demo"
        rahul = Lead.objects.get(name="Rahul Verma")
        assert rahul.stage == "won" and rahul.won_at is not None and rahul.company.name == "A Customer"  # matched existing
        assert "Imported from zoho" in rahul.consent_note
    client.post(reverse("imports:rollback", args=[job.id]))
    with tenant_context(two_tenants["a"].id):
        assert not Lead.objects.filter(name__in=["Priya Sharma", "Rahul Verma"]).exists()
        assert not Company.objects.filter(name="Omega Diagnostics").exists()
        assert Company.objects.filter(name="A Customer").exists()  # pre-existing company untouched


def test_import_requires_permission_and_is_tenant_scoped(two_tenants, login, client):
    login(two_tenants["tc_a"])
    assert client.get(reverse("imports:index")).status_code == 403
    assert _upload(client, COMPANIES_CSV).status_code == 403
    client.logout()
    login(two_tenants["owner_a"])
    _upload(client, COMPANIES_CSV)
    job = ImportJob.all_objects.get()
    client.logout()
    login(two_tenants["owner_b"])
    assert client.get(reverse("imports:review", args=[job.id])).status_code == 404
    assert client.get(reverse("imports:index")).status_code == 200 and b"data.csv" not in client.get(reverse("imports:index")).content


def test_money_and_date_parsing():
    assert services.parse_money_paise("₹1,20,000") == 120000_00
    assert services.parse_money_paise("1.2L") == 120000_00
    assert services.parse_money_paise("2 Cr") == 2_00_00_000_00
    assert services.parse_money_paise("abc") is None
    assert services.parse_date("15/11/2026") == date(2026, 11, 15)
    assert services.parse_date("2026-11-15 10:30") == date(2026, 11, 15)
    assert services.parse_date("15 Nov 2026") == date(2026, 11, 15)
    assert services.parse_date("sometime") is None


# ---------- audit viewer ----------

def test_audit_viewer_filters_and_permission(two_tenants, login, client):
    client_mgr = login(two_tenants["mgr_a"])
    client_mgr.post(reverse("approvals:approve", args=[two_tenants["appr"].id]))
    client.logout()
    login(two_tenants["tc_a"])
    assert client.get(reverse("core:audit")).status_code == 403
    client.logout()
    login(two_tenants["owner_a"])
    r = client.get(reverse("core:audit"))
    assert r.status_code == 200 and b"approval.approve" in r.content
    r = client.get(reverse("core:audit"), {"group": "approval"})
    assert b"approval.approve" in r.content
    r = client.get(reverse("core:audit"), {"group": "import"})
    assert b"approval.approve" not in r.content
    r = client.get(reverse("core:audit"), {"actor": two_tenants["tc_a"].id})
    assert b"approval.approve" not in r.content
    # tenant B's owner never sees tenant A's log
    client.logout()
    login(two_tenants["owner_b"])
    assert b"approval.approve" not in client.get(reverse("core:audit")).content
