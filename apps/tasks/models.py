"""
Tasks and projects (foundation document: "Projects, My Tasks; link tasks to company and
contract"). A task stands alone or belongs to a project, and may point at a lead or a
company so it shows on their pages. Contracts link in Phase 2.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel


class Project(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        DONE = "done", "Completed"

    COLORS = [("brand", "Navy"), ("green", "Green"), ("yellow", "Amber"), ("red", "Red"), ("info", "Blue"), ("grey", "Grey")]

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    company = models.ForeignKey("crm.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_projects")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="projects")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    color = models.CharField(max_length=10, choices=COLORS, default="brand")
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_date", "name"]
        indexes = [models.Index(fields=["tenant", "status"])]

    def __str__(self):
        return self.name

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status != self.Status.DONE and self.due_date < timezone.localdate())


class Task(TenantModel):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        DOING = "doing", "In progress"
        DONE = "done", "Done"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    lead = models.ForeignKey("crm.Lead", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    company = models.ForeignKey("crm.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.TODO)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_date", "-priority", "-created_at"]
        indexes = [models.Index(fields=["tenant", "assignee", "status", "due_date"]), models.Index(fields=["tenant", "project", "status"])]

    def __str__(self):
        return self.title

    @property
    def is_done(self):
        return self.status == self.Status.DONE

    @property
    def is_overdue(self):
        return bool(self.due_date and not self.is_done and self.due_date < timezone.localdate())

    def set_status(self, status: str):
        self.status = status
        self.done_at = timezone.now() if status == self.Status.DONE else None
        self.save(update_fields=["status", "done_at", "updated_at"])
