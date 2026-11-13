"""Fixed rules that turn metrics into strengths and weaknesses. Each signal carries its number."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    kind: str  # "strength" | "weakness"
    text: str


def is_financial(fundamentals: dict) -> bool:
    """Banks and NBFCs run on leverage, so debt and liquidity ratios mean something else there."""
    sector = (fundamentals.get("sector") or "").lower()
    industry = (fundamentals.get("industry") or "").lower()
    return "financial" in sector or any(w in industry for w in ("bank", "credit", "insurance", "capital markets"))


ASSET_LIGHT_SECTORS = {"technology"}  # not communication services: telecom carriers own networks and towers
ASSET_LIGHT_INDUSTRIES = {
    "software",
    "internet",
    "information technology",
    "it services",
    "consulting",
}


def is_asset_light(fundamentals: dict) -> bool:
    """Asset-light and IP-driven businesses where physical book value is low or unrepresentative."""
    sector = (fundamentals.get("sector") or "").lower()
    industry = (fundamentals.get("industry") or "").lower()
    if any(s in sector for s in ASSET_LIGHT_SECTORS):
        return True
    return any(w in industry for w in ASSET_LIGHT_INDUSTRIES)


def is_india(fundamentals: dict) -> bool:
    return (
        fundamentals.get("currency") == "INR"
        or fundamentals.get("financial_currency") == "INR"
        or fundamentals.get("country") == "India"
    )


def is_fund(fundamentals: dict) -> bool:
    """ETFs and mutual funds: company rules (ROE, debt, profit) don't apply to them."""
    kind = fundamentals.get("quote_type")
    if kind:
        return kind.upper() in ("ETF", "MUTUALFUND")
    # Fundamentals cached before quote_type was kept: a fund has no sector and no annual statements.
    return not fundamentals.get("sector") and not fundamentals.get("annual")


def bands(m: dict, fundamentals: dict) -> dict[str, str]:
    """Plain-word grades for the ratios, so the LLM does not have to judge thresholds itself."""
    fin = is_financial(fundamentals)

    def grade(v, cuts):
        if v is None:
            return None
        return next(label for limit, label in cuts if limit is None or v < limit)

    out = {
        "roe": grade(m.get("roe"), [(10, "low"), (15, "average"), (None, "high")]),
        "net_margin": grade(m.get("net_margin"), [(0, "loss-making"), (5, "thin"), (15, "average"), (None, "strong")]),
        "pe": grade(m.get("pe"), [(0, "negative earnings"), (15, "modest"), (35, "moderate"), (60, "rich"), (None, "very expensive")]),
    }
    if not fin:
        out["debt_to_equity"] = grade(m.get("debt_to_equity"), [(0.3, "low"), (1.0, "moderate"), (1.5, "elevated"), (None, "high")])
        out["current_ratio"] = grade(m.get("current_ratio"), [(1, "tight"), (2, "adequate"), (None, "comfortable")])
    return {k: v for k, v in out.items() if v}


def evaluate(m: dict, fundamentals: dict) -> list[Signal]:
    s: list[Signal] = []

    def add(kind, text):
        s.append(Signal(kind, text))

    fin = is_financial(fundamentals)
    if m.get("quarter_stale"):
        # Old quarters say nothing about current momentum; judge on the remaining numbers.
        m = {k: v for k, v in m.items() if k not in ("rev_yoy", "ni_yoy", "op_margin_change_yoy")}

    if (v := m.get("rev_yoy")) is not None:
        if v >= 15:
            add("strength", f"Revenue up {v:.1f}% year on year in the latest quarter")
        elif v < 0:
            add("weakness", f"Revenue down {abs(v):.1f}% year on year in the latest quarter")
    if (v := m.get("ni_yoy")) is not None:
        if v >= 15:
            add("strength", f"Net profit up {v:.1f}% year on year in the latest quarter")
        elif v <= -10:
            add("weakness", f"Net profit down {abs(v):.1f}% year on year in the latest quarter")
    if (v := m.get("op_margin_change_yoy")) is not None and not fin:
        if v >= 2:
            add("strength", f"Operating margin widened {v:.1f} pts year on year (now {m['q_op_margin']:.1f}%)")
        elif v <= -2:
            add("weakness", f"Operating margin shrank {abs(v):.1f} pts year on year (now {m['q_op_margin']:.1f}%)")
    if (v := m.get("rev_cagr")) is not None:
        yrs = m.get("rev_cagr_years")
        if v >= 12:
            add("strength", f"Revenue grew {v:.1f}% a year over the last {yrs} years")
        elif v <= 0:
            add("weakness", f"Revenue has not grown over the last {yrs} years ({v:.1f}% a year)")

    if (v := m.get("roe")) is not None:
        if v >= 15:
            add("strength", f"High return on equity: {v:.1f}%")
        elif v < 10:
            add("weakness", f"Low return on equity: {v:.1f}%")
    if (v := m.get("net_margin")) is not None:
        if v >= 15:
            add("strength", f"Strong net profit margin: {v:.1f}%")
        elif v < 0:
            add("weakness", f"Loss-making: net margin {v:.1f}%")
        elif v < 5 and not fin:
            add("weakness", f"Thin net profit margin: {v:.1f}%")

    if not fin:
        if (v := m.get("debt_to_equity")) is not None:
            if v <= 0.3:
                add("strength", f"Low debt: debt/equity {v:.2f}")
            elif v >= 1.5:
                add("weakness", f"High debt: debt/equity {v:.2f}")
        if (v := m.get("current_ratio")) is not None:
            if v >= 2:
                add("strength", f"Comfortable liquidity: current ratio {v:.2f}")
            elif v < 1:
                add("weakness", f"Tight liquidity: current ratio {v:.2f} (short-term liabilities exceed assets)")
        years, pos = m.get("fcf_years") or 0, m.get("fcf_positive_years") or 0
        if years >= 3 and pos == years:
            add("strength", f"Positive free cash flow in each of the last {years} years")
        elif m.get("fcf_latest") is not None and m["fcf_latest"] < 0:
            add("weakness", "Negative free cash flow in the latest year")

    if (v := m.get("pe")) is not None:
        if v > 60:
            add("weakness", f"Expensive: P/E {v:.1f}")
        elif 0 < v < 15:
            add("strength", f"Modest valuation: P/E {v:.1f}")
    if (v := m.get("from_52w_high")) is not None and v <= -30:
        add("weakness", f"Trading {abs(v):.0f}% below its 52-week high")
    return s
