"""GST helpers: state codes (place of supply), intra/inter decision, amount in words."""
from __future__ import annotations

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat", "26": "Dadra & Nagar Haveli and Daman & Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman & Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}
STATE_CHOICES = [(k, f"{k} · {v}") for k, v in STATE_CODES.items()]
NAME_TO_CODE = {v.lower(): k for k, v in STATE_CODES.items()}


def state_code_from_gstin(gstin: str) -> str:
    g = (gstin or "").strip()
    return g[:2] if len(g) >= 2 and g[:2] in STATE_CODES else ""


def state_code_from_name(name: str) -> str:
    return NAME_TO_CODE.get((name or "").strip().lower(), "")


def tax_mode(supplier_state: str, place_of_supply: str) -> str:
    """Same state → CGST + SGST ('intra'); different state → IGST ('inter'). Unknown → intra."""
    if supplier_state and place_of_supply and supplier_state != place_of_supply:
        return "inter"
    return "intra"


def amount_to_words(paise: int) -> str:
    """Indian grouping, e.g. 'Twelve Lakh Thirty-Four Thousand Five Hundred Rupees and Sixty Paise'."""
    paise = int(paise or 0)
    rupees, p = divmod(abs(paise), 100)
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two(x):
        return ones[x] if x < 20 else tens[x // 10] + ((" " + ones[x % 10]) if x % 10 else "")

    def three(x):
        return (ones[x // 100] + " Hundred" + ((" " + two(x % 100)) if x % 100 else "")) if x >= 100 else two(x)

    def words(n):
        if n == 0:
            return "Zero"
        parts = []
        for div, label in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
            q, n = divmod(n, div)
            if q:
                parts.append(f"{three(q)} {label}")
        if n:
            parts.append(three(n))
        return " ".join(parts)

    out = f"{words(rupees)} Rupees"
    if p:
        out += f" and {two(p)} Paise"
    return out + " Only"
