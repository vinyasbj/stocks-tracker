"""Number formatting: crores and lakhs for INR, billions and millions for everything else."""

from datetime import date

SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def indian_group(n: int) -> str:
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + s


def money(v: float | None, currency: str | None) -> str:
    if v is None:
        return "n/a"
    sym = SYMBOLS.get(currency or "", (currency or "") + " ")
    if currency == "INR":
        if abs(v) >= 1e7:
            return f"{sym}{indian_group(round(v / 1e7))} Cr"
        return f"{sym}{indian_group(round(v))}"
    for size, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= size:
            return f"{sym}{v / size:,.2f}{unit}"
    return f"{sym}{v:,.2f}"


def amount(v: float | None, currency: str | None, signed: bool = False) -> str:
    """Exact money for positions and P&L: ₹1,23,456 / +$1,234.50 (money() rounds to crores)."""
    if v is None:
        return "n/a"
    sym = SYMBOLS.get(currency or "", (currency or "") + " ")
    sign = ("+" if v >= 0 else "-") if signed else ("-" if v < 0 else "")
    body = indian_group(round(abs(v))) if currency == "INR" else f"{abs(v):,.2f}"
    return f"{sign}{sym}{body}"


def price(v: float | None, currency: str | None) -> str:
    if v is None:
        return "n/a"
    return f"{SYMBOLS.get(currency or '', (currency or '') + ' ')}{v:,.2f}"


def pct(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def quarter_label(period: str | None) -> str:
    if not period:
        return "n/a"
    return date.fromisoformat(period).strftime("%b %Y")
