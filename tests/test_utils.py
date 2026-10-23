from apps.core.money import rupees, short_rupees
from apps.core.phones import normalize_phone, validate_gstin


def test_phone_normalisation_handles_indian_mobile_std_and_australia():
    assert normalize_phone("98480 12345") == "+919848012345"
    assert normalize_phone("040-2345 6789") == "+914023456789"
    assert normalize_phone("+91 98480 12345") == "+919848012345"
    assert normalize_phone("0412 345 678", default_country="61") == "+61412345678"
    assert normalize_phone("1") == ""  # the junk lead from the old dashboard
    assert normalize_phone("") == ""


def test_gstin_checksum():
    assert validate_gstin("27AAPFU0939F1ZV") is True
    assert validate_gstin("27AAPFU0939F1ZW") is False
    assert validate_gstin("not-a-gstin") is False


def test_money_formatting_uses_indian_grouping_and_paise():
    assert rupees(1234567_00) == "₹12,34,567"
    assert rupees(1234567_50) == "₹12,34,567.50"
    assert rupees(999_00) == "₹999"
    assert short_rupees(720440_00) == "₹7.2L"
    assert short_rupees(1_30_00_000_00) == "₹1.3Cr"
