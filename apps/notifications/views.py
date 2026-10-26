from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification, NotificationPreference


@login_required
def index(request):
    qs = Notification.objects.filter(user=request.user)[:100]
    return render(request, "notifications/index.html", {"items": qs, "unread": Notification.objects.filter(user=request.user, read_at__isnull=True).count()})


@login_required
@require_POST
def read_all(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notifications:index")


@login_required
def open_one(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    n.mark_read()
    return redirect(n.url or "notifications:index")


@login_required
@require_POST
def prefs(request):
    p = NotificationPreference.for_user(request.user)
    p.email_approvals = bool(request.POST.get("email_approvals"))
    p.email_reminders = bool(request.POST.get("email_reminders"))
    p.email_tasks = bool(request.POST.get("email_tasks"))
    p.save()
    messages.success(request, "Email preferences saved.")
    return redirect("accounts:profile")
