from django.conf import settings
from django.core.mail import send_mail


def send_otp(email: str, code: str):
    send_mail(
        "Your QuantumLoop sign-in code",
        f"Your sign-in code is {code}. It expires in {settings.OTP_CODE_TTL_SECONDS // 60} minutes.\n\nIf you did not request it, ignore this email.",
        settings.DEFAULT_FROM_EMAIL,
        [email],
    )


def send_invite(inv, token: str):
    link = f"{settings.SITE_URL}/accounts/invite/{token}/"
    send_mail(
        f"{inv.invited_by.name} invited you to {inv.tenant.name} on QuantumLoop",
        f"{inv.invited_by.name} has invited you to join {inv.tenant.name} as {inv.get_role_display()}.\n\nAccept the invitation:\n{link}\n\nThe link expires in {settings.INVITE_TTL_DAYS} days.",
        settings.DEFAULT_FROM_EMAIL,
        [inv.email],
    )
