"""
Validate → dry run → commit → rollback (section 11).

Data rules:
- Match duplicates on normalised phone, email, GSTIN and a fuzzy company name.
  Suggest merges; never merge automatically (duplicates are skipped and listed).
- Keep original created dates and owners where the owner exists; else assign to the importer.
- Rows that fail validation go to a needs-fixing list and are never dropped silently.
- Every row keeps its source name, original ID and raw values.
"""
from __future__ import annotations

import difflib
import re
from datetime import date, datetime, timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership
from apps.core.phones import normalize_phone, validate_gstin
from apps.crm.models import Activity, Company, Contact, Lead

from .models import ImportJob, ImportRow
from .specs import FIELDS

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%b %d, %Y", "%m/%d/%Y")
FUZZY_NAME = 0.92


# ---------- parsing helpers ----------

def parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s[: len(datetime.now().strftime(fmt)) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def parse_money_paise(s: str) -> int | None:
    s = (s or "").strip().replace("₹", "").replace(",", "").replace("Rs.", "").replace("Rs", "").replace("INR", "").strip()
    if not s:
        return None
    mult = 1
    low = s.lower()
    if low.endswith("l") or low.endswith("lakh"):
        mult, s = 100000, re.sub(r"[a-z]+$", "", low)
    elif low.endswith("cr"):
        mult, s = 10000000, low[:-2]
    elif low.endswith("k"):
        mult, s = 1000, low[:-1]
    try:
        return int(round(float(s) * mult * 100))
    except ValueError:
        return None


def mapped_values(job: ImportJob, raw: dict) -> dict:
    """Apply the column → field mapping; several columns on one field are joined with a space."""
    out: dict[str, str] = {}
    for col, fld in job.field_mapping.items():
        v = (raw.get(col) or "").strip()
        if not v:
            continue
        out[fld] = f"{out[fld]} {v}".strip() if fld in out else v
    return out


# ---------- validation / dry run ----------

class Validator:
    def __init__(self, job: ImportJob, importer):
        self.job = job
        self.importer = importer
        self.entity = job.entity
        self.required = [f.key for f in FIELDS[self.entity] if f.required]
        self.members = {m.user.email.lower(): m.user for m in Membership.objects.filter(tenant_id=job.tenant_id, is_active=True).select_related("user")}
        # existing records for duplicate checks
        if self.entity == "companies":
            qs = Company.objects.values("id", "name", "phone_normalized", "email", "gstin", "source_system", "external_id")
        else:
            qs = Lead.objects.values("id", "name", "phone_normalized", "email", "source_system", "external_id")
        self.existing = list(qs)
        self.by_phone = {r["phone_normalized"]: r for r in self.existing if r.get("phone_normalized")}
        self.by_email = {r["email"].lower(): r for r in self.existing if r.get("email")}
        self.by_gstin = {r["gstin"].upper(): r for r in self.existing if r.get("gstin")}
        self.by_ext = {(r["source_system"], r["external_id"]): r for r in self.existing if r.get("external_id")}
        self.names = {r["name"].strip().lower(): r for r in self.existing}
        self.seen_phone: dict[str, int] = {}
        self.seen_email: dict[str, int] = {}
        self.seen_gstin: dict[str, int] = {}
        self.seen_ext: dict[str, int] = {}

    def run(self) -> dict:
        counts = {"new": 0, "update": 0, "skip": 0, "error": 0}
        rows = list(self.job.rows.all())
        for row in rows:
            row.mapped, row.status, row.messages, row.duplicate_of = {}, ImportRow.Status.NEW, [], ""
            self._validate(row)
            counts[row.status] += 1
        ImportRow.objects.bulk_update(rows, ["mapped", "status", "messages", "duplicate_of"], batch_size=500)
        return counts

    def _validate(self, row: ImportRow):
        m = mapped_values(self.job, row.raw)
        errors, notes = [], []
        for key in self.required:
            if not m.get(key):
                errors.append(f"{key.replace('_', ' ').title()} is required")
        # phone
        if m.get("phone"):
            norm = normalize_phone(m["phone"])
            if not norm:
                errors.append(f"Phone '{m['phone']}' is not a valid number")
            m["phone_normalized"] = norm
        if m.get("contact_phone"):
            m["contact_phone_normalized"] = normalize_phone(m["contact_phone"])
        # email
        if m.get("email"):
            m["email"] = m["email"].lower()
            if not EMAIL_RE.match(m["email"]):
                errors.append(f"Email '{m['email']}' is not valid")
        # gstin
        if m.get("gstin"):
            m["gstin"] = m["gstin"].upper().replace(" ", "")
            if not validate_gstin(m["gstin"]):
                errors.append(f"GSTIN '{m['gstin']}' fails the format or checksum")
        # dates
        for key in ("created_at", "next_follow_up"):
            if m.get(key):
                d = parse_date(m[key])
                if d is None:
                    errors.append(f"Could not read the date '{m[key]}' for {key.replace('_', ' ')}")
                else:
                    m[key] = d.isoformat()
        if m.get("est_value"):
            p = parse_money_paise(m["est_value"])
            if p is None:
                errors.append(f"Could not read the amount '{m['est_value']}'")
            else:
                m["est_value_paise"] = p
        if m.get("owner_email"):
            if m["owner_email"].lower() not in self.members:
                notes.append(f"Owner '{m['owner_email']}' is not a user here; will be assigned to you")
                m.pop("owner_email")
        if self.entity == "leads" and m.get("stage"):
            m["stage_code"] = self.job.stage_map.get(m["stage"], "new")
        if m.get("tags"):
            m["tags_list"] = [t.strip() for t in re.split(r"[,;|]", m["tags"]) if t.strip()]
        row.mapped = m
        if errors:
            row.status, row.messages = ImportRow.Status.ERROR, errors + notes
            return
        # re-import of the same external id → update, not duplicate
        ext = (self.job.source, m.get("external_id", ""))
        if m.get("external_id"):
            if ext in self.seen_ext:
                row.status, row.messages = ImportRow.Status.SKIP, [f"Same Original ID as row {self.seen_ext[ext]} in this file"] + notes
                return
            self.seen_ext[ext] = row.row_no
            hit = self.by_ext.get(ext)
            if hit:
                row.status, row.messages, row.duplicate_of = ImportRow.Status.UPDATE, [f"Updates existing record '{hit['name']}' (same Original ID)"] + notes, hit["name"]
                m["_update_id"] = hit["id"]
                return
        # duplicates within the file
        dup_in_file = None
        for key, seen in (("phone_normalized", self.seen_phone), ("email", self.seen_email), ("gstin", self.seen_gstin)):
            v = m.get(key)
            if v:
                if v in seen:
                    dup_in_file = f"Same {key.replace('_normalized', '')} as row {seen[v]} in this file"
                    break
                seen[v] = row.row_no
        if dup_in_file:
            row.status, row.messages = ImportRow.Status.SKIP, [dup_in_file] + notes
            return
        # duplicates against existing records (suggest, never merge)
        hit = None
        for key, index in (("phone_normalized", self.by_phone), ("email", self.by_email), ("gstin", self.by_gstin)):
            v = m.get(key)
            if v and v in index:
                hit, why = index[v], key.replace("_normalized", "")
                break
        if hit is None and self.entity == "companies":
            nm = m["name"].strip().lower()
            if nm in self.names:
                hit, why = self.names[nm], "name"
            else:
                close = difflib.get_close_matches(nm, self.names.keys(), n=1, cutoff=FUZZY_NAME)
                if close:
                    hit, why = self.names[close[0]], f"similar name '{self.names[close[0]]['name']}'"
        if hit:
            row.status, row.duplicate_of = ImportRow.Status.SKIP, hit["name"]
            row.messages = [f"Possible duplicate of '{hit['name']}' (same {why}). Merge it by hand if it is the same record."] + notes
            return
        row.messages = notes


# ---------- commit ----------

class Committer:
    def __init__(self, job: ImportJob, importer):
        self.job, self.importer = job, importer
        self.members = {m.user.email.lower(): m.user for m in Membership.objects.filter(tenant_id=job.tenant_id, is_active=True).select_related("user")}

    def _owner(self, m):
        return self.members.get((m.get("owner_email") or "").lower(), self.importer)

    @transaction.atomic
    def run(self) -> dict:
        done = {"created": 0, "updated": 0}
        rows = list(self.job.rows.filter(status__in=[ImportRow.Status.NEW, ImportRow.Status.UPDATE]))
        for row in rows:
            m = row.mapped
            if self.job.entity == "companies":
                self._company(row, m)
            else:
                self._lead(row, m)
            done["created" if row.status == ImportRow.Status.NEW else "updated"] += 1
        ImportRow.objects.bulk_update(rows, ["object_id", "created_ids", "before"], batch_size=500)
        return done

    def _company_fields(self, m):
        return {"name": m["name"], "gstin": m.get("gstin", ""), "phone": m.get("phone", ""), "email": m.get("email", ""), "address": m.get("address", ""),
                "city": m.get("city", ""), "state": m.get("state", ""), "source": m.get("source", ""), "tags": m.get("tags_list", [])}

    def _company(self, row, m):
        if m.get("_update_id"):
            c = Company.objects.get(pk=m["_update_id"])
            row.before = {k: getattr(c, k) for k in self._company_fields(m)}
            for k, v in self._company_fields(m).items():
                if v:
                    setattr(c, k, v)
            c.save()
            row.object_id = c.id
            return
        c = Company(**self._company_fields(m), owner=self._owner(m), source_system=self.job.source, external_id=m.get("external_id", ""))
        c.created_by = self.importer
        c.save()
        if m.get("created_at"):
            Company.all_objects.filter(pk=c.pk).update(created_at=datetime.fromisoformat(m["created_at"]).replace(tzinfo=timezone.get_current_timezone()))
        row.object_id, row.created_ids = c.id, {"company": c.id}
        if m.get("contact_name"):
            ct = Contact(company=c, name=m["contact_name"], phone=m.get("contact_phone", ""), designation=m.get("contact_designation", ""), is_primary=True)
            ct.created_by = self.importer
            ct.save()
            row.created_ids["contact"] = ct.id

    def _lead_fields(self, m):
        return {"name": m["name"], "phone": m.get("phone", ""), "email": m.get("email", ""), "source": m.get("source", ""), "consent_note": m.get("consent_note", "") or f"Imported from {self.job.source} file {self.job.filename}",
                "est_value_paise": m.get("est_value_paise"), "next_follow_up": m.get("next_follow_up") or None, "stage": m.get("stage_code", "new")}

    def _lead(self, row, m):
        company = None
        if m.get("company_name"):
            company = Company.objects.filter(name__iexact=m["company_name"].strip()).first()
            if company is None:
                company = Company(name=m["company_name"].strip(), owner=self._owner(m), source_system=self.job.source)
                company.created_by = self.importer
                company.save()
                row.created_ids = {"company": company.id}
        if m.get("_update_id"):
            lead = Lead.objects.get(pk=m["_update_id"])
            row.before = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in {k: getattr(lead, k) for k in self._lead_fields(m)}.items()}
            for k, v in self._lead_fields(m).items():
                if v not in (None, ""):
                    setattr(lead, k, v)
            if company:
                lead.company = company
            lead.save()
            row.object_id = lead.id
            return
        lead = Lead(**self._lead_fields(m), company=company, owner=self._owner(m), source_system=self.job.source, external_id=m.get("external_id", ""))
        if lead.stage == Lead.Stage.WON:
            lead.won_at = timezone.now()
        lead.created_by = self.importer
        lead.save()
        if m.get("created_at"):
            Lead.all_objects.filter(pk=lead.pk).update(created_at=datetime.fromisoformat(m["created_at"]).replace(tzinfo=timezone.get_current_timezone()))
        row.object_id = lead.id
        row.created_ids = {**row.created_ids, "lead": lead.id}
        if m.get("notes"):
            a = Activity(lead=lead, company=company, type=Activity.Type.NOTE, notes=m["notes"])
            a.created_by = self.importer
            a.save()
            row.created_ids["activity"] = a.id


# ---------- rollback ----------

@transaction.atomic
def rollback(job: ImportJob) -> dict:
    """Undo a committed batch: soft-delete what it created, restore what it updated."""
    undone = {"deleted": 0, "restored": 0}
    model = Company if job.entity == "companies" else Lead
    for row in job.rows.filter(status__in=[ImportRow.Status.NEW, ImportRow.Status.UPDATE]):
        if row.status == ImportRow.Status.UPDATE and row.before and row.object_id:
            obj = model.objects.filter(pk=row.object_id).first()
            if obj:
                for k, v in row.before.items():
                    if k == "next_follow_up" and v:
                        v = date.fromisoformat(v)
                    setattr(obj, k, v)
                obj.save()
                undone["restored"] += 1
            continue
        for kind, pk in row.created_ids.items():
            cls = {"company": Company, "contact": Contact, "lead": Lead, "activity": Activity}[kind]
            obj = cls.objects.filter(pk=pk).first()
            if obj:
                obj.soft_delete()
                undone["deleted"] += 1
    job.status = ImportJob.Status.ROLLED_BACK
    job.rolled_back_at = timezone.now()
    job.save(update_fields=["status", "rolled_back_at", "updated_at"])
    return undone
