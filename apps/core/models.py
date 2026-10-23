"""
Base model every tenant-owned table follows (foundation document, section 8):
tenant_id, created_by, created_at, updated_at and a soft-delete flag.
Money is stored in paise as integers; timestamps in UTC.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from .tenancy import get_current_tenant_id


class TenantQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)

    def for_current_tenant(self):
        tid = get_current_tenant_id()
        return self.filter(tenant_id=tid) if tid is not None else self.none()


class TenantManager(models.Manager):
    """Default manager: current tenant only, soft-deleted rows hidden."""

    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db).for_current_tenant().alive()


class AllTenantsManager(models.Manager):
    """Escape hatch for platform tasks and tests. Combine with `rls_bypass()` on Postgres."""

    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db)


class TenantModel(models.Model):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="+", editable=False, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = TenantManager()
    all_objects = AllTenantsManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            tid = get_current_tenant_id()
            if tid is None:
                raise ValueError(f"{type(self).__name__} saved outside a tenant context")
            self.tenant_id = tid
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


class AuditLog(models.Model):
    """Append-only. Approvals, exports, role changes and support sessions all land here."""

    tenant = models.ForeignKey("tenants.Tenant", null=True, on_delete=models.CASCADE, related_name="+", db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=64, db_index=True)  # e.g. "approval.approve", "role.change", "export.leads"
    model = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = AllTenantsManager()

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["tenant", "-at"])]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.model}#{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Audit log rows are append-only")
        if self.tenant_id is None:
            self.tenant_id = get_current_tenant_id()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit log rows cannot be deleted")
