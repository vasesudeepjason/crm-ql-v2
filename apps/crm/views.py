from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render

from apps.core.permissions import require_perm

from .models import Company, Lead
from .scoping import scoped


@login_required
@require_perm("leads.view")
def lead_list(request):
    qs = scoped(request, Lead.objects.select_related("company", "owner"), "leads.view")
    stage = request.GET.get("stage", "")
    q = request.GET.get("q", "").strip()
    if stage:
        qs = qs.filter(stage=stage)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(company__name__icontains=q) | Q(phone_normalized__icontains=q))
    counts = dict(scoped(request, Lead.objects.all(), "leads.view").values_list("stage").annotate(n=Count("id")))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    ctx = {"page": page, "stage": stage, "q": q, "stages": Lead.Stage.choices, "counts": counts, "total": sum(counts.values())}
    if request.htmx:
        return render(request, "crm/_lead_rows.html", ctx)
    return render(request, "crm/leads.html", ctx)


@login_required
@require_perm("companies.view")
def company_list(request):
    qs = scoped(request, Company.objects.select_related("owner").annotate(lead_count=Count("leads")).order_by("name"), "companies.view")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(gstin__icontains=q))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "crm/companies.html", {"page": page, "q": q})
