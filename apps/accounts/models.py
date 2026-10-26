from __future__ import annotations

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.crypto import decrypt, encrypt

from .roles import ROLE_CHOICES, ROLES


def _hash(value: str) -> str:
    return hashlib.sha256(f"{settings.SECRET_KEY}:{value}".encode()).hexdigest()


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
    totp_secret_enc = models.CharField(max_length=255, blank=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    def __str__(self):
        return self.name or self.email

    @property
    def first_name(self):
        return (self.name or "").split(" ")[0]

    # ---- TOTP (section 9: TOTP for owners and admins) ----
    def set_totp_secret(self, secret: str):
        self.totp_secret_enc = encrypt(secret)

    def totp_secret(self) -> str:
        return decrypt(self.totp_secret_enc)

    def verify_totp(self, code: str) -> bool:
        import pyotp

        secret = self.totp_secret()
        return bool(secret) and pyotp.TOTP(secret).verify((code or "").replace(" ", ""), valid_window=1)

    def active_membership(self):
        """One user holds one role per tenant in v1; most users belong to one tenant."""
        return self.memberships.select_related("tenant", "tenant__plan").filter(is_active=True).order_by("id").first()


class Membership(models.Model):
    rls_exempt = True  # read by the middleware before a tenant is chosen; see apps/core/rls.py

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


class OneTimeCode(models.Model):
    """Six-digit email sign-in code. Stored hashed; expires in OTP_CODE_TTL_SECONDS; five tries."""

    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    MAX_ATTEMPTS = 5

    @classmethod
    def issue(cls, email: str):
        code = f"{secrets.randbelow(10**6):06d}"
        cls.objects.filter(email=email, used_at__isnull=True).update(used_at=timezone.now())  # one live code per email
        otc = cls.objects.create(email=email, code_hash=_hash(code), expires_at=timezone.now() + timedelta(seconds=settings.OTP_CODE_TTL_SECONDS))
        return otc, code

    @classmethod
    def consume(cls, email: str, code: str) -> bool:
        otc = cls.objects.filter(email=email, used_at__isnull=True).order_by("-created_at").first()
        if otc is None or otc.expires_at < timezone.now() or otc.attempts >= cls.MAX_ATTEMPTS:
            return False
        otc.attempts += 1
        if otc.code_hash != _hash((code or "").strip()):
            otc.save(update_fields=["attempts"])
            return False
        otc.used_at = timezone.now()
        otc.save(update_fields=["attempts", "used_at"])
        return True


class Invitation(models.Model):
    rls_exempt = True  # accepted by token before the user belongs to any tenant

    """An admin invites an email into the tenant with a role; accepting creates the user and membership."""

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    manager = models.ForeignKey(Membership, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "email"])]

    def __str__(self):
        return f"{self.email} -> {self.tenant} as {self.role}"

    @property
    def is_open(self):
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()

    @classmethod
    def create(cls, *, tenant, email, role, invited_by, manager=None):
        token = secrets.token_urlsafe(32)
        cls.objects.filter(tenant=tenant, email=email.lower(), accepted_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now())
        inv = cls.objects.create(
            tenant=tenant, email=email.lower(), role=role, manager=manager, invited_by=invited_by, token_hash=_hash(token),
            expires_at=timezone.now() + timedelta(days=settings.INVITE_TTL_DAYS),
        )
        return inv, token

    @classmethod
    def by_token(cls, token: str):
        return cls.objects.select_related("tenant", "invited_by").filter(token_hash=_hash(token or "")).first()

    def accept(self, *, name: str, password) -> Membership:
        if not self.is_open:
            raise ValueError("This invitation is no longer valid.")
        user = User.objects.filter(email=self.email).first()
        if user is None:
            user = User.objects.create_user(self.email, password, name=name)
        elif name and not user.name:
            user.name = name
            user.save(update_fields=["name"])
        m, _ = Membership.objects.update_or_create(user=user, tenant=self.tenant, defaults={"role": self.role, "manager": self.manager, "is_active": True})
        self.accepted_at = timezone.now()
        self.save(update_fields=["accepted_at"])
        return m
