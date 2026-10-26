"""In-app notifications (the bell) and per-user email preferences. Emails are sent by apps/notifications/services.py."""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel


class Notification(TenantModel):
    class Kind(models.TextChoices):
        APPROVAL_SUBMITTED = "approval_submitted", "Approval submitted"
        APPROVAL_DECIDED = "approval_decided", "Approval decided"
        FOLLOW_UPS = "follow_ups", "Follow-ups due"
        STALE_APPROVALS = "stale_approvals", "Approvals waiting"
        TASK_ASSIGNED = "task_assigned", "Task assigned"
        TASKS_DUE = "tasks_due", "Tasks due"
        CONTRACT_RENEWAL = "contract_renewal", "Renewal"
        LEAVE = "leave", "Leave"
        SYSTEM = "system", "System"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=300, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "user", "read_at"])]

    def __str__(self):
        return f"{self.user} · {self.title}"

    @property
    def is_read(self):
        return self.read_at is not None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at", "updated_at"])


class NotificationPreference(models.Model):
    """Which emails a user wants. In-app notifications are always created."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    email_approvals = models.BooleanField(default=True, help_text="Decisions on what I submitted, and new items for me to review")
    email_reminders = models.BooleanField(default=True, help_text="Morning digest of follow-ups, tasks due and approvals waiting")
    email_tasks = models.BooleanField(default=True, help_text="When someone assigns me a task")

    def __str__(self):
        return f"prefs for {self.user}"

    @classmethod
    def for_user(cls, user) -> "NotificationPreference":
        obj, _ = cls.objects.get_or_create(user=user)
        return obj
