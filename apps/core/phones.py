"""
Phone normalisation (section 8). Numbers are stored as typed and in E.164 form for
matching and duplicate checks. Handles +91 mobiles, Indian STD-code landlines such
as 040, and +61 numbers seen in the existing data.
"""
from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")


def normalize_phone(raw: str | None, default_country: str = "91") -> str:
    if not raw:
        return ""
    s = raw.strip()
    plus = s.startswith("+")
    digits = _DIGITS.sub("", s)
    if not digits or len(digits) < 6:
        return ""  # junk such as "1" never normalises
    if plus:
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if default_country == "91":
        if len(digits) == 10 and digits[0] in "6789":
            return "+91" + digits
        if len(digits) == 11 and digits[0] == "0":  # 0 + STD + number, or 0 + mobile
            return "+91" + digits[1:]
        if len(digits) == 12 and digits.startswith("91"):
            return "+" + digits
        if len(digits) in (10, 11) and digits[0] == "0":
            return "+91" + digits[1:]
    if default_country == "61":
        if len(digits) == 10 and digits[0] == "0":
            return "+61" + digits[1:]
        if len(digits) == 11 and digits.startswith("61"):
            return "+" + digits
    return "+" + default_country + digits.lstrip("0")


GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def validate_gstin(gstin: str) -> bool:
    """Format and checksum (section 8)."""
    g = (gstin or "").strip().upper()
    if not GSTIN_RE.match(g):
        return False
    total = 0
    for i, ch in enumerate(g[:-1]):
        v = _GST_CHARS.index(ch)
        factor = 2 if i % 2 else 1
        p = v * factor
        total += p // 36 + p % 36
    check = _GST_CHARS[(36 - total % 36) % 36]
    return check == g[-1]
