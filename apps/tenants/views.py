"""
Platform support console (QuantumLoop staff) and the tenant-side Workspace settings:
details, support-access visibility, DPDP export and deletion request.
"""
import csv
import io
import zipfile
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.mail import mail_admins
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Membership
from apps.approvals.models import Approval
from apps.core.audit import audit
from apps.core.models import AuditLog
from apps.core.permissions import require_perm
from apps.core.tenancy import rls_bypass
from apps.crm.models import Activity, Company, Contact, Lead

from .models import SupportSession, Tenant

TIMEZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Australia/Sydney", "Europe/London", "America/New_York", "UTC"]


# ---------- platform staff ----------

@staff_member_required
def support_console(request):
    q = request.GET.get("q", "").strip()
    with rls_bypass():
        tenants = Tenant.objects.select_related("plan").annotate(members=Count("memberships", filter=Q(memberships__is_active=True))).order_by("name")
        if q:
            tenants = tenants.filter(Q(name__icontains=q) | Q(slug__icontains=q))
        active = SupportSession.objects.filter(ended_at__isnull=True, expires_at__gt=timezone.now()).select_related("tenant", "staff")
        recent = SupportSession.objects.select_related("tenant", "staff", "ended_by")[:30]
    return render(request, "platform/support.html", {"tenants": tenants, "active": active, "recent": recent, "q": q, "current": getattr(request, "support_session", None)})


@staff_member_required
@require_POST
def support_start(request, tenant_id):
    reason = request.POST.get("reason", "").strip()
    minutes = min(max(int(request.POST.get("minutes") or SupportSession.DEFAULT_MINUTES), 5), 240)
    if len(reason) < 5:
        messages.error(request, "Give a reason (ticket number or what you are checking). It is shown to the customer.")
        return redirect("tenants:support")
    with rls_bypass():
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        for old in SupportSession.objects.filter(staff=request.user, ended_at__isnull=True):
            old.end(by=request.user, reason="left")
        ss = SupportSession.objects.create(tenant=tenant, staff=request.user, reason=reason, expires_at=timezone.now() + timedelta(minutes=minutes))
        AuditLog.objects.create(tenant=tenant, actor=request.user, action="support.start", model="SupportSession", object_id=str(ss.pk), after={"reason": reason, "minutes": minutes}, ip=request.META.get("REMOTE_ADDR"))
    request.session["support_session_id"] = ss.pk
    messages.success(request, f"You are inside {tenant.name} for {minutes} minutes. Everything you do is logged for their admins.")
    return redirect("dashboard:home")


@login_required
@require_POST
def support_leave(request):
    ss = getattr(request, "support_session", None)
    if ss is not None:
        ss.end(by=request.user, reason="left")
        AuditLog.objects.create(tenant=ss.tenant, actor=request.user, action="support.end", model="SupportSession", object_id=str(ss.pk), note="left")
    request.session.pop("support_session_id", None)
    return redirect("tenants:support")


# ---------- workspace settings (tenant admins) ----------

@login_required
@require_perm("settings.manage")
def workspace(request):
    t = request.tenant
    if request.method == "POST":
        before = {"name": t.name, "timezone": t.timezone, "gstin": t.gstin, "address": t.address}
        t.name = request.POST.get("name", t.name).strip() or t.name
        tz = request.POST.get("timezone", t.timezone)
        t.timezone = tz if tz in TIMEZONES else t.timezone
        t.gstin = request.POST.get("gstin", "").strip().upper()
        t.address = request.POST.get("address", "").strip()
        if t.gstin:
            from apps.core.phones import validate_gstin

            if not validate_gstin(t.gstin):
                messages.error(request, "That GSTIN fails the format or checksum.")
                return redirect("tenants:workspace")
        with rls_bypass():
            t.save(update_fields=["name", "timezone", "gstin", "address"])
        audit(request, "workspace.update", t, before=before, after={"name": t.name, "timezone": t.timezone, "gstin": t.gstin, "address": t.address})
        messages.success(request, "Workspace details saved.")
        return redirect("tenants:workspace")
    from apps.invoices.forms import InvoiceSettingsForm
    from apps.invoices.models import InvoiceSettings

    inv_cfg = InvoiceSettings.for_tenant(t)
    sessions = SupportSession.objects.filter(tenant=t).select_related("staff", "ended_by")[:20]
    counts = {"companies": Company.objects.count(), "contacts": Contact.objects.count(), "leads": Lead.objects.count(), "activities": Activity.objects.count(), "approvals": Approval.objects.count(), "users": Membership.objects.filter(tenant=t, is_active=True).count()}
    return render(request, "settings/workspace.html", {"t": t, "timezones": TIMEZONES, "sessions": sessions, "active_sessions": [s for s in sessions if s.is_active], "counts": counts, "section": "workspace", "inv_cfg": inv_cfg, "inv_form": InvoiceSettingsForm(instance=inv_cfg)})


@login_required
@require_perm("settings.manage")
@require_POST
def revoke_support(request, pk):
    ss = get_object_or_404(SupportSession, pk=pk, tenant=request.tenant)
    if ss.is_active:
        ss.end(by=request.user, reason="revoked")
        audit(request, "support.revoke", ss, note=f"ended {ss.staff.name}'s access")
        messages.success(request, f"{ss.staff.name}'s support access has been ended.")
    return redirect("tenants:workspace")


def _csv(rows, fields):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(fields)
    for r in rows:
        w.writerow([r.get(f, "") for f in fields])
    return out.getvalue()


@login_required
@require_perm("data.export")
@require_POST
def export_all(request):
    """DPDP: per-tenant export of all data, as a zip of CSVs (section 9)."""
    t = request.tenant
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("companies.csv", _csv(Company.objects.values("id", "name", "gstin", "phone", "email", "address", "city", "state", "source", "tags", "created_at", "owner__email"), ["id", "name", "gstin", "phone", "email", "address", "city", "state", "source", "tags", "created_at", "owner__email"]))
        z.writestr("contacts.csv", _csv(Contact.objects.values("id", "company_id", "company__name", "name", "designation", "phone", "email", "is_primary", "do_not_contact"), ["id", "company_id", "company__name", "name", "designation", "phone", "email", "is_primary", "do_not_contact"]))
        z.writestr("leads.csv", _csv(Lead.objects.values("id", "name", "company__name", "phone", "email", "stage", "status", "owner__email", "source", "est_value_paise", "next_follow_up", "consent_note", "do_not_contact", "created_at", "won_at"), ["id", "name", "company__name", "phone", "email", "stage", "status", "owner__email", "source", "est_value_paise", "next_follow_up", "consent_note", "do_not_contact", "created_at", "won_at"]))
        z.writestr("activities.csv", _csv(Activity.objects.values("id", "lead_id", "company_id", "type", "outcome", "duration_seconds", "notes", "created_by__email", "created_at"), ["id", "lead_id", "company_id", "type", "outcome", "duration_seconds", "notes", "created_by__email", "created_at"]))
        z.writestr("approvals.csv", _csv(Approval.objects.values("id", "kind", "title", "company_name", "status", "reason", "submitter__email", "reviewer__email", "submitted_at", "decided_at"), ["id", "kind", "title", "company_name", "status", "reason", "submitter__email", "reviewer__email", "submitted_at", "decided_at"]))
        z.writestr("users.csv", _csv(Membership.objects.filter(tenant=t).values("user__name", "user__email", "role", "job_title", "is_active", "created_at"), ["user__name", "user__email", "role", "job_title", "is_active", "created_at"]))
        z.writestr("audit_log.csv", _csv(AuditLog.objects.filter(tenant=t).values("at", "actor__email", "action", "model", "object_id", "note", "ip"), ["at", "actor__email", "action", "model", "object_id", "note", "ip"]))
        z.writestr("README.txt", f"QuantumLoop export for {t.name} ({t.slug})\nGenerated {timezone.now():%Y-%m-%d %H:%M} UTC by {request.user.email}\nMoney columns are in paise; timestamps are UTC.\n")
    audit(request, "export.all_data", t)
    resp = HttpResponse(buf.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{t.slug}-export-{timezone.now():%Y%m%d}.zip"'
    return resp


@login_required
@require_perm("billing.manage")
@require_POST
def request_deletion(request):
    """DPDP: deletion on request. Owner asks; platform confirms and runs it (never silent, never instant)."""
    t = request.tenant
    reason = request.POST.get("reason", "").strip()
    audit(request, "workspace.deletion_requested", t, note=reason[:200])
    try:
        mail_admins(f"Deletion requested: {t.name} ({t.slug})", f"{request.user.name} <{request.user.email}> requested deletion of workspace {t.name}.\nReason: {reason}\n\nConfirm with the customer, export their data, then run the deletion.")
    except Exception:
        pass
    messages.success(request, "Deletion request recorded. QuantumLoop will confirm with you before anything is removed; export your data first if you have not.")
    return redirect("tenants:workspace")
