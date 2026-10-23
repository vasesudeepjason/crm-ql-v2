from __future__ import annotations

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .roles import ROLE_CHOICES, ROLES


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """A login. Tenant access comes through Membership; `is_staff` marks QuantumLoop platform staff."""

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="QuantumLoop platform staff")
    date_joined = models.DateTimeField(default=timezone.now)
    totp_enabled = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    def __str__(self):
        return self.name or self.email

    @property
    def first_name(self):
        return (self.name or "").split(" ")[0]

    def active_membership(self):
        """One user holds one role per tenant in v1; most users belong to one tenant."""
        return self.memberships.select_related("tenant", "tenant__plan").filter(is_active=True).order_by("id").first()


class Membership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    job_title = models.CharField(max_length=80, blank=True, help_text="Label only; the role decides access")
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("user", "tenant")]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"

    @property
    def role_name(self):
        return ROLES[self.role]["name"]

    def permissions(self) -> frozenset[str]:
        return frozenset(ROLES[self.role]["perms"].keys())

    def has_perm(self, perm: str) -> bool:
        return perm in ROLES[self.role]["perms"]

    def scope_for(self, perm: str) -> str | None:
        return ROLES[self.role]["perms"].get(perm)

    def team_user_ids(self) -> list[int]:
        """Users whose records a 'team'-scoped permission covers: self plus direct reports."""
        return [self.user_id, *self.reports.filter(is_active=True).values_list("user_id", flat=True)]
