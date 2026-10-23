"""Money helpers. Amounts are stored in paise (int); never floats."""


def rupees(paise: int | None) -> str:
    """₹ with Indian grouping: 1234567 paise → ₹12,345.67; whole rupees drop decimals."""
    if paise is None:
        return "—"
    neg = paise < 0
    paise = abs(paise)
    r, p = divmod(paise, 100)
    s = str(r)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    out = f"₹{s}" + (f".{p:02d}" if p else "")
    return "-" + out if neg else out


def short_rupees(paise: int | None) -> str:
    """Compact: ₹7.2L, ₹1.3Cr, ₹38K."""
    if paise is None:
        return "—"
    r = paise / 100
    if r >= 1e7:
        return f"₹{r / 1e7:.1f}Cr"
    if r >= 1e5:
        return f"₹{r / 1e5:.1f}L"
    if r >= 1e3:
        return f"₹{r / 1e3:.0f}K"
    return f"₹{r:.0f}"
