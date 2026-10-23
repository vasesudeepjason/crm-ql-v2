import re

import pyotp
import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.models import Invitation, Membership, OneTimeCode, User
from apps.core.models import AuditLog
from apps.core.tenancy import rls_bypass

from .conftest import PW


@pytest.fixture(autouse=True)
def _clear_throttles():
    cache.clear()


# ---------- one-time code sign-in ----------

def test_otp_sign_in_end_to_end(two_tenants, client):
    r = client.post(reverse("accounts:otp"), {"email": "owner@a.test"})
    assert r.status_code == 302 and r.url == reverse("accounts:otp_verify")
    assert len(mail.outbox) == 1 and "owner@a.test" in mail.outbox[0].to
    code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
    r = client.post(reverse("accounts:otp_verify"), {"code": code})
    assert r.status_code == 302 and r.url == reverse("dashboard:home")
    assert client.get(reverse("dashboard:home")).status_code == 200
    with rls_bypass():
        assert AuditLog.objects.filter(action="auth.login", note="otp").exists()


def test_otp_wrong_code_and_single_use(two_tenants, client):
    client.post(reverse("accounts:otp"), {"email": "owner@a.test"})
    code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
    r = client.post(reverse("accounts:otp_verify"), {"code": "000000"})
    assert r.status_code == 200 and b"wrong or has expired" in r.content
    assert client.post(reverse("accounts:otp_verify"), {"code": code}).status_code == 302
    assert OneTimeCode.consume("owner@a.test", code) is False  # already used


def test_otp_unknown_email_sends_nothing_but_looks_the_same(db, client):
    r = client.post(reverse("accounts:otp"), {"email": "nobody@a.test"})
    assert r.status_code == 302 and mail.outbox == []


def test_otp_request_is_rate_limited(two_tenants, client):
    for _ in range(5):
        client.post(reverse("accounts:otp"), {"email": "owner@a.test"})
    r = client.post(reverse("accounts:otp"), {"email": "owner@a.test"})
    assert r.status_code == 200 and b"Too many codes" in r.content
    assert len(mail.outbox) == 5


def test_password_login_is_rate_limited(two_tenants, client):
    for _ in range(10):
        client.post(reverse("accounts:login"), {"username": "owner@a.test", "password": "wrong"})
    r = client.post(reverse("accounts:login"), {"username": "owner@a.test", "password": PW})
    assert r.status_code == 200 and b"Too many attempts" in r.content


# ---------- TOTP ----------

def test_totp_setup_then_login_requires_second_step(two_tenants, client, login):
    owner = two_tenants["owner_a"]
    login(owner)
    r = client.get(reverse("accounts:totp_setup"))
    assert r.status_code == 200 and b"<svg" in r.content
    secret = client.session["totp_pending"]
    r = client.post(reverse("accounts:totp_setup"), {"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 302
    owner.refresh_from_db()
    assert owner.totp_enabled and owner.totp_secret() == secret and owner.totp_secret_enc != secret

    client.logout()
    r = client.post(reverse("accounts:login"), {"username": owner.email, "password": PW})
    assert r.status_code == 302 and r.url == reverse("accounts:twofa")
    assert client.get(reverse("dashboard:home")).status_code == 302  # not signed in yet
    r = client.post(reverse("accounts:twofa"), {"code": "123456"})
    assert r.status_code == 200 and b"wrong" in r.content
    r = client.post(reverse("accounts:twofa"), {"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 302 and client.get(reverse("dashboard:home")).status_code == 200


def test_totp_disable_needs_password(two_tenants, client, login):
    owner = two_tenants["owner_a"]
    owner.set_totp_secret(pyotp.random_base32())
    owner.totp_enabled = True
    owner.save()
    login(owner)
    client.post(reverse("accounts:totp_disable"), {"password": "nope"})
    owner.refresh_from_db()
    assert owner.totp_enabled
    client.post(reverse("accounts:totp_disable"), {"password": PW})
    owner.refresh_from_db()
    assert not owner.totp_enabled and owner.totp_secret_enc == ""


# ---------- invitations ----------

def test_invite_accept_creates_user_and_membership(two_tenants, client, login):
    login(two_tenants["owner_a"])
    r = client.post(reverse("accounts:invite"), {"email": "New.Person@a.test", "role": "telecaller", "manager": two_tenants["m_mgr"].id})
    assert r.status_code == 302
    assert len(mail.outbox) == 1
    token = re.search(r"/accounts/invite/([^/\s]+)/", mail.outbox[0].body).group(1)
    client.logout()

    r = client.get(reverse("accounts:invite_accept", args=[token]))
    assert r.status_code == 200 and b"A Co" in r.content
    r = client.post(reverse("accounts:invite_accept", args=[token]), {"name": "New Person", "password": "a-long-password-9"})
    assert r.status_code == 302
    m = Membership.objects.get(user__email="new.person@a.test")
    assert m.tenant == two_tenants["a"] and m.role == "telecaller" and m.manager == two_tenants["m_mgr"]
    assert client.get(reverse("dashboard:home")).status_code == 200  # signed in after accepting
    # token is single-use
    assert client.get(reverse("accounts:invite_accept", args=[token])).status_code == 410


def test_invite_revoke_and_bad_token(two_tenants, client, login):
    login(two_tenants["owner_a"])
    client.post(reverse("accounts:invite"), {"email": "x@a.test", "role": "sales"})
    inv = Invitation.objects.get(email="x@a.test")
    client.post(reverse("accounts:revoke_invite", args=[inv.id]))
    token = re.search(r"/accounts/invite/([^/\s]+)/", mail.outbox[0].body).group(1)
    client.logout()
    assert client.get(reverse("accounts:invite_accept", args=[token])).status_code == 410
    assert client.get(reverse("accounts:invite_accept", args=["garbage"])).status_code == 410


def test_invite_respects_plan_user_limit(two_tenants, client, login):
    tenant = two_tenants["a"]
    from apps.tenants.models import Plan

    tenant.plan = Plan.objects.create(code="tiny", name="Tiny", price_paise_month=0, users_included=4, storage_gb=1)
    tenant.save()
    login(two_tenants["owner_a"])  # tenant A already has 4 active members
    client.post(reverse("accounts:invite"), {"email": "one-too-many@a.test", "role": "sales"})
    assert not Invitation.objects.filter(email="one-too-many@a.test").exists()


def test_telecaller_cannot_open_users_page_or_invite(two_tenants, client, login):
    login(two_tenants["tc_a"])
    assert client.get(reverse("accounts:users")).status_code == 403
    assert client.post(reverse("accounts:invite"), {"email": "x@a.test", "role": "sales"}).status_code == 403


# ---------- roles ----------

def test_role_change_is_audited_and_last_owner_protected(two_tenants, client, login):
    login(two_tenants["owner_a"])
    tc = two_tenants["tc_a"].memberships.get()
    r = client.post(reverse("accounts:change_role", args=[tc.id]), {"role": "sales"})
    assert r.status_code == 302
    tc.refresh_from_db()
    assert tc.role == "sales"
    with rls_bypass():
        log = AuditLog.objects.get(action="role.change", object_id=str(tc.id))
        assert log.before["role"] == "telecaller" and log.after["role"] == "sales"
    # the only owner cannot demote or deactivate themself, and no-one can remove the last owner
    me = two_tenants["owner_a"].memberships.get()
    client.post(reverse("accounts:change_role", args=[me.id]), {"role": "admin"})
    me.refresh_from_db()
    assert me.role == "owner"
    assert client.post(reverse("accounts:toggle_active", args=[me.id])).status_code == 302
    me.refresh_from_db()
    assert me.is_active


def test_admin_cannot_touch_owner_or_grant_owner(two_tenants, client, login):
    admin = User.objects.create_user("admin@a.test", PW, name="Admin A")
    Membership.objects.create(user=admin, tenant=two_tenants["a"], role="admin")
    login(admin)
    owner_m = two_tenants["owner_a"].memberships.get()
    assert client.post(reverse("accounts:change_role", args=[owner_m.id]), {"role": "viewer"}).status_code == 403
    tc = two_tenants["tc_a"].memberships.get()
    assert client.post(reverse("accounts:change_role", args=[tc.id]), {"role": "owner"}).status_code == 403


def test_deactivated_user_loses_access(two_tenants, client, login):
    login(two_tenants["owner_a"])
    tc = two_tenants["tc_a"].memberships.get()
    client.post(reverse("accounts:toggle_active", args=[tc.id]))
    client.logout()
    login(two_tenants["tc_a"])
    assert client.get(reverse("dashboard:home")).status_code == 403
