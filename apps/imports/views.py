import csv
import hashlib
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import audit
from apps.core.permissions import require_perm
from apps.crm.models import Lead

from . import services
from .models import ImportJob, ImportRow, SavedMapping
from .parsers import ParseError, parse
from .specs import FIELDS, SOURCES, auto_map, guess_stage


def _columns_key(columns):
    return hashlib.sha256("|".join(c.strip().lower() for c in columns).encode()).hexdigest()[:64]


@login_required
@require_perm("data.import")
def index(request):
    jobs = ImportJob.objects.select_related("created_by")[:50]
    return render(request, "imports/index.html", {"jobs": jobs, "section": "imports"})


@login_required
@require_perm("data.import")
def new(request):
    if request.method == "POST":
        entity = request.POST.get("entity")
        source = request.POST.get("source", "generic")
        f = request.FILES.get("file")
        if entity not in FIELDS or source not in SOURCES or f is None:
            messages.error(request, "Choose what to import, the source, and a file.")
            return redirect("imports:new")
        try:
            columns, rows = parse(f.name, f.read())
        except ParseError as e:
            messages.error(request, str(e))
            return redirect("imports:new")
        f.seek(0)
        job = ImportJob(entity=entity, source=source, file=f, filename=f.name, columns=columns, row_count=len(rows))
        job.created_by = request.user
        saved = SavedMapping.objects.filter(entity=entity, source=source, columns_key=_columns_key(columns)).first()
        job.mapping = saved.mapping if saved else auto_map(entity, source, columns)
        job.save()
        ImportRow.objects.bulk_create([ImportRow(tenant_id=job.tenant_id, job=job, row_no=i + 2, raw=r, created_by=request.user) for i, r in enumerate(rows)], batch_size=500)
        audit(request, "import.upload", job, after={"entity": entity, "source": source, "rows": len(rows), "file": f.name})
        if saved:
            messages.success(request, f"Loaded your saved mapping '{saved.name}'.")
        return redirect("imports:map", pk=job.pk)
    return render(request, "imports/new.html", {"sources": sorted(SOURCES.items(), key=lambda kv: (kv[1]["priority"], kv[1]["name"])), "entities": ImportJob.Entity.choices, "section": "imports"})


@login_required
@require_perm("data.import")
def template_csv(request, entity):
    if entity not in FIELDS:
        return HttpResponse(status=404)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f.label for f in FIELDS[entity]])
    sample = {"companies": ["Sri Lakshmi Traders", "36AABCS1234A1Z5", "+91 98480 12345", "accounts@srilakshmi.in", "12 MG Road", "Hyderabad", "Telangana", "", "Referral", "AMC, Priority", "Ramesh Rao", "+91 98480 54321", "Purchase Manager", "C-1001", "2025-04-01"],
              "leads": ["Deccan Infra Pvt Ltd", "Deccan Infra Pvt Ltd", "+91 99887 76655", "info@deccaninfra.in", "Interested", "", "IndiaMART", "48000", "2026-11-05", "Wants a demo next week", "Enquiry via IndiaMART", "L-2001", "2026-10-01"]}[entity]
    w.writerow(sample)
    resp = HttpResponse(out.getvalue(), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="quantumloop-{entity}-template.csv"'
    return resp


@login_required
@require_perm("data.import")
def map_columns(request, pk):
    job = get_object_or_404(ImportJob, pk=pk)
    if job.status in (ImportJob.Status.COMMITTED, ImportJob.Status.ROLLED_BACK):
        return redirect("imports:review", pk=job.pk)
    fields = FIELDS[job.entity]
    sample_rows = list(job.rows.all()[:3])
    if request.method == "POST":
        mapping = {}
        for col in job.columns:
            fld = request.POST.get(f"col:{col}", "")
            if fld and fld in {f.key for f in fields}:
                mapping[col] = fld
        if "name" not in mapping.values():
            messages.error(request, "Map at least the name column.")
            return redirect("imports:map", pk=job.pk)
        if job.entity == "leads" and "stage" in mapping.values():
            stage_col = next(c for c, f in mapping.items() if f == "stage")
            values = sorted({(r.raw.get(stage_col) or "").strip() for r in job.rows.all()} - {""})
            mapping["stage_map"] = {v: request.POST.get(f"stage:{v}", guess_stage(v)) for v in values}
        job.mapping = mapping
        job.status = ImportJob.Status.MAPPED
        job.save(update_fields=["mapping", "status", "updated_at"])
        if request.POST.get("save_mapping"):
            name = request.POST.get("mapping_name") or f"{SOURCES[job.source]['name']} · {job.get_entity_display()}"
            sm, _ = SavedMapping.objects.update_or_create(entity=job.entity, source=job.source, columns_key=_columns_key(job.columns), defaults={"name": name, "mapping": mapping, "created_by": request.user})
        counts = services.Validator(job, request.user).run()
        job.counts, job.status, job.validated_at = counts, ImportJob.Status.VALIDATED, timezone.now()
        job.save(update_fields=["counts", "status", "validated_at", "updated_at"])
        audit(request, "import.validate", job, after=counts)
        return redirect("imports:review", pk=job.pk)
    stage_values, stage_choices = [], []
    if job.entity == "leads":
        stage_col = next((c for c, f in job.field_mapping.items() if f == "stage"), None)
        if stage_col:
            stage_values = sorted({(r.raw.get(stage_col) or "").strip() for r in job.rows.all()} - {""})
        stage_choices = Lead.Stage.choices
    ctx = {"job": job, "fields": fields, "mapping": job.field_mapping, "samples": sample_rows, "stage_values": stage_values, "stage_map": job.stage_map, "stage_choices": stage_choices, "guess": {v: guess_stage(v) for v in stage_values}, "section": "imports"}
    return render(request, "imports/map.html", ctx)


@login_required
@require_perm("data.import")
def review(request, pk):
    job = get_object_or_404(ImportJob.objects.select_related("created_by"), pk=pk)
    if job.status in (ImportJob.Status.UPLOADED, ImportJob.Status.MAPPED):
        return redirect("imports:map", pk=job.pk)
    tab = request.GET.get("tab", "error" if job.counts.get("error") else "new")
    return render(request, "imports/review.html", {"job": job, "tab": tab, "section": "imports", **_rows_ctx(job, tab, request.GET.get("page"))})


def _rows_ctx(job, tab, page_no):
    qs = job.rows.all()
    if tab in ("new", "update", "skip", "error"):
        qs = qs.filter(status=tab)
    page = Paginator(qs, 50).get_page(page_no)
    shown = [c for c, f in job.field_mapping.items()][:6]
    return {"page": page, "tab": tab, "shown_cols": shown, "row_tabs": [("new", "New"), ("update", "Updated"), ("skip", "Skipped"), ("error", "Needs fixing"), ("all", "All")]}


@login_required
@require_perm("data.import")
def rows(request, pk):
    job = get_object_or_404(ImportJob, pk=pk)
    tab = request.GET.get("tab", "new")
    return render(request, "imports/_rows.html", {"job": job, **_rows_ctx(job, tab, request.GET.get("page"))})


@login_required
@require_perm("data.import")
def errors_csv(request, pk):
    """The downloadable needs-fixing file: original columns plus a 'Problems' column."""
    job = get_object_or_404(ImportJob, pk=pk)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Row", *job.columns, "Status", "Problems"])
    for r in job.rows.filter(status__in=[ImportRow.Status.ERROR, ImportRow.Status.SKIP]):
        w.writerow([r.row_no, *[r.raw.get(c, "") for c in job.columns], r.get_status_display(), "; ".join(r.messages)])
    audit(request, "export.import_errors", job)
    resp = HttpResponse(out.getvalue(), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="needs-fixing-{job.pk}.csv"'
    return resp


@login_required
@require_perm("data.import")
@require_POST
def commit(request, pk):
    job = get_object_or_404(ImportJob, pk=pk)
    if job.status != ImportJob.Status.VALIDATED:
        messages.error(request, "Run the dry run first.")
        return redirect("imports:review", pk=job.pk)
    try:
        done = services.Committer(job, request.user).run()
    except Exception as e:  # keep the job, surface the problem, nothing half-written (atomic)
        job.status, job.error = ImportJob.Status.FAILED, str(e)[:2000]
        job.save(update_fields=["status", "error", "updated_at"])
        messages.error(request, f"Import failed and was rolled back: {e}")
        return redirect("imports:review", pk=job.pk)
    job.status, job.committed_at = ImportJob.Status.COMMITTED, timezone.now()
    job.counts = {**job.counts, **done}
    job.save(update_fields=["status", "committed_at", "counts", "updated_at"])
    audit(request, "import.commit", job, after=done)
    messages.success(request, f"Imported {done['created']} new and updated {done['updated']} {job.get_entity_display().lower()}. You can roll this back until {job.rollback_deadline:%d %b}.")
    return redirect("imports:review", pk=job.pk)


@login_required
@require_perm("data.import")
@require_POST
def rollback_job(request, pk):
    job = get_object_or_404(ImportJob, pk=pk)
    if not job.can_rollback:
        messages.error(request, "This import can no longer be rolled back.")
        return redirect("imports:review", pk=job.pk)
    undone = services.rollback(job)
    audit(request, "import.rollback", job, after=undone)
    messages.success(request, f"Rolled back: {undone['deleted']} records removed, {undone['restored']} restored.")
    return redirect("imports:review", pk=job.pk)
