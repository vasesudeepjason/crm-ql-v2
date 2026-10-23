"""
Per-field encryption for secrets we must store but never show: TOTP seeds now,
bank details, ID numbers and salaries later (section 9).

Key: FIELD_ENCRYPTION_KEY env var (a Fernet key: `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`).
In DEBUG or under tests without one, a key is derived from SECRET_KEY so local setups just work.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
    if not key:
        if not settings.DEBUG and not getattr(settings, "TESTING", False):
            raise RuntimeError("FIELD_ENCRYPTION_KEY is required outside DEBUG")
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode() if text else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
