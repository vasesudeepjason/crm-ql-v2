"""
What can be imported, and how file headers map onto our fields.

Source templates carry the column names other tools export, so a Zoho or Tally file
maps itself; "generic" relies on the alias list alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    required: bool = False
    hint: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


COMPANY_FIELDS = [
    Field("name", "Company name", True, aliases=("company", "company name", "account name", "account", "party name", "customer", "customer name", "organisation", "organization", "ledger name", "name")),
    Field("gstin", "GSTIN", aliases=("gstin", "gst no", "gst number", "gstin/uin", "tax id")),
    Field("phone", "Phone", aliases=("phone", "mobile", "phone number", "mobile number", "contact number", "telephone")),
    Field("email", "Email", aliases=("email", "e-mail", "email address", "email id")),
    Field("address", "Address", aliases=("address", "billing address", "street", "mailing street")),
    Field("city", "City", aliases=("city", "billing city", "mailing city")),
    Field("state", "State", aliases=("state", "billing state", "mailing state")),
    Field("owner_email", "Owner (email)", hint="Must match a user in this workspace; otherwise assigned to you", aliases=("owner", "owner email", "account owner", "assigned to", "sales rep")),
    Field("source", "Source", aliases=("source", "lead source", "account source", "channel")),
    Field("tags", "Tags", hint="Comma separated", aliases=("tags", "tag", "labels", "category")),
    Field("contact_name", "Contact name", aliases=("contact", "contact name", "contact person", "primary contact")),
    Field("contact_phone", "Contact phone", aliases=("contact phone", "contact mobile")),
    Field("contact_designation", "Contact designation", aliases=("designation", "title", "job title")),
    Field("external_id", "Original ID", hint="Lets a re-import update instead of duplicate", aliases=("id", "account id", "record id", "customer id", "party id", "external id")),
    Field("created_at", "Created on", hint="Keeps the original date", aliases=("created", "created on", "created time", "created date", "date added")),
]

LEAD_FIELDS = [
    Field("name", "Lead name", True, hint="Person or company the lead is about", aliases=("lead name", "name", "full name", "first name", "last name", "contact name", "person")),
    Field("company_name", "Company", hint="Matched to an existing company or created", aliases=("company", "company name", "account", "account name", "organisation", "organization", "firm")),
    Field("phone", "Phone", aliases=("phone", "mobile", "phone number", "mobile number", "contact number", "telephone")),
    Field("email", "Email", aliases=("email", "e-mail", "email address", "email id")),
    Field("stage", "Stage", hint="Mapped to your pipeline on the next step", aliases=("stage", "lead status", "status", "pipeline stage", "lead stage")),
    Field("owner_email", "Owner (email)", hint="Must match a user in this workspace; otherwise assigned to you", aliases=("owner", "owner email", "lead owner", "assigned to", "sales rep", "telecaller")),
    Field("source", "Source", aliases=("source", "lead source", "channel", "campaign")),
    Field("est_value", "Estimated value (₹)", aliases=("value", "amount", "deal value", "expected revenue", "estimated value", "budget")),
    Field("next_follow_up", "Next follow-up", aliases=("next follow up", "next follow-up", "follow up", "follow-up date", "next action date", "callback")),
    Field("notes", "Notes", hint="Imported as an activity", aliases=("notes", "description", "remarks", "comments", "note")),
    Field("consent_note", "Consent note", hint="DPDP: how the data was captured", aliases=("consent", "consent note")),
    Field("external_id", "Original ID", hint="Lets a re-import update instead of duplicate", aliases=("id", "lead id", "record id", "external id", "enquiry id")),
    Field("created_at", "Created on", hint="Keeps the original date", aliases=("created", "created on", "created time", "created date", "date added", "enquiry date")),
]

FIELDS = {"companies": COMPANY_FIELDS, "leads": LEAD_FIELDS}

# Source templates: extra header → field hints for tools our customers already run (section 11, Migration B).
SOURCES = {
    "generic": {"name": "Excel or CSV (our template)", "priority": 1, "headers": {}},
    "zoho": {"name": "Zoho CRM / Bigin export", "priority": 2, "headers": {
        "companies": {"account name": "name", "account owner": "owner_email", "billing street": "address", "billing city": "city", "billing state": "state", "account id": "external_id", "created time": "created_at"},
        "leads": {"first name": "name", "last name": "name", "company": "company_name", "lead status": "stage", "lead owner": "owner_email", "lead source": "source", "lead id": "external_id", "created time": "created_at", "mobile": "phone", "description": "notes"},
    }},
    "tally": {"name": "Tally customers (Excel/XML export)", "priority": 2, "headers": {
        "companies": {"ledger name": "name", "name": "name", "gstin/uin": "gstin", "mailing name": "contact_name", "address": "address", "state": "state", "e-mail": "email", "mobile": "phone"},
    }},
    "indiamart": {"name": "IndiaMART / JustDial lead export", "priority": 3, "headers": {
        "leads": {"sender name": "name", "sender company": "company_name", "sender mobile": "phone", "sender email": "email", "query time": "created_at", "query message": "notes", "query id": "external_id", "product name": "source"},
    }},
    "freshsales": {"name": "Freshsales / LeadSquared / HubSpot CSV", "priority": 3, "headers": {
        "leads": {"first name": "name", "last name": "name", "company": "company_name", "stage": "stage", "owner": "owner_email", "lead source": "source", "created at": "created_at", "mobile": "phone", "mobile phone": "phone"},
    }},
}


def auto_map(entity: str, source: str, columns: list[str]) -> dict[str, str]:
    """Suggest a column → field mapping from the source template and the alias list."""
    fields = FIELDS[entity]
    source_headers = SOURCES.get(source, SOURCES["generic"])["headers"].get(entity, {})
    alias_index: dict[str, str] = {}
    for f in fields:
        for a in f.aliases:
            alias_index.setdefault(a, f.key)
    out: dict[str, str] = {}
    taken: set[str] = set()
    for col in columns:
        key = col.strip().lower()
        target = source_headers.get(key) or alias_index.get(key)
        if target and (target not in taken or target == "name"):  # several name parts may join into one name
            out[col] = target
            taken.add(target)
    return out


STAGE_GUESSES = [
    ("won", "won"), ("closed won", "won"), ("converted", "won"), ("lost", "lost"), ("closed lost", "lost"), ("junk", "lost"), ("not interested", "lost"),
    ("negoti", "negotiation"), ("propos", "proposal"), ("quote", "proposal"), ("qualif", "qualified"), ("interest", "interested"), ("hot", "interested"),
    ("follow", "follow_up"), ("callback", "follow_up"), ("contact", "contacted"), ("attempt", "attempted"), ("not contacted", "attempted"), ("new", "new"), ("open", "new"),
]


def guess_stage(value: str) -> str:
    v = (value or "").strip().lower()
    for needle, stage in STAGE_GUESSES:
        if needle in v:
            return stage
    return "new"
