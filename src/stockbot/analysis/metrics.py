"""Derived numbers from the fundamentals dict built by data.market.fetch_fundamentals."""

import math
import statistics
from datetime import date

from .signals import is_india


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1) * 100 * (1 if old > 0 else -1)


def _margin(row: dict, field: str) -> float | None:
    if not row or row.get(field) is None or not row.get("revenue"):
        return None
    return row[field] / row["revenue"] * 100


def year_ago(quarters: list[dict], latest: dict) -> dict | None:
    """The quarter ending ~12 months before `latest`. Matched by date because Yahoo skips quarters."""
    target = date.fromisoformat(latest["period"])
    for q in quarters:
        days = (target - date.fromisoformat(q["period"])).days
        if 320 <= days <= 410:
            return q
    return None


def cagr(new: float | None, old: float | None, years: int) -> float | None:
    if not new or not old or old <= 0 or new <= 0 or years <= 0:
        return None
    return ((new / old) ** (1 / years) - 1) * 100


def loss_streak(annual: list[dict]) -> int | None:
    """Fiscal years in a row, counting back from the latest, that ended in a net loss. None without data."""
    incomes = [r.get("net_income") for r in annual]
    if not incomes or incomes[0] is None:
        return None
    streak = 0
    for x in incomes:
        if x is None or x >= 0:
            break
        streak += 1
    return streak


STALE_AFTER_DAYS = 200  # a quarter plus the filing deadline plus slack

# Market-cap bands in the price currency: (large at or above, mid at or above); below that is small.
CAP_BANDS = {"USD": (10e9, 2e9), "INR": (80_000e7, 25_000e7)}  # ₹80,000 cr and ₹25,000 cr
CAP_PROFIT_HURDLE = {"large": 10.0, "mid": 15.0, "small": 20.0}  # profit CAGR each band must reach, in %
MIN_PE_YEARS = 3  # past year-end P/Es needed for a median


def cap_band(market_cap: float | None, currency: str | None) -> str | None:
    bands = CAP_BANDS.get(currency or "")
    if not market_cap or bands is None:
        return None
    return "large" if market_cap >= bands[0] else "mid" if market_cap >= bands[1] else "small"


def year_end_pes(annual: list[dict]) -> list[tuple[str, float, float]]:
    """(period, P/E, price) at each fiscal year end with a profit and a known price, newest first."""
    return [
        (r["period"], r["price_end"] / r["eps"], r["price_end"])
        for r in annual
        if r.get("price_end") and (r.get("eps") or 0) > 0
    ]


def pe_history(f: dict, m: dict, today: date) -> None:
    """P/E against its own past: the median of past year ends, and the change since about a year ago.

    Past P/Es are price at year end over that year's EPS, so they need the price and the statements in
    the same currency (INFY.NS is priced in INR but reports in USD).
    """
    if f.get("financial_currency") and f.get("currency") and f["financial_currency"] != f["currency"]:
        why = f"priced in {f['currency']} but reports in {f['financial_currency']}"
        m["pe_vs_median_why"] = m["pe_contraction_why"] = why
        return
    pes = year_end_pes(f.get("annual") or [])
    pe, price = m.get("pe"), f.get("price")
    if len(pes) >= MIN_PE_YEARS:
        median = statistics.median(p for _, p, _ in pes)
        m["pe_median"], m["pe_median_years"] = median, len(pes)
        if pe and pe > 0:
            m["pe_vs_median"] = pe / median
    else:
        m["pe_vs_median_why"] = f"only {len(pes)} profitable year ends with a price"
    # "A year ago": the year end nearest 12 months back (6 to 18 months), since statements are yearly.
    past = [(abs((today - date.fromisoformat(d)).days - 365), d, p, px) for d, p, px in pes
            if 180 <= (today - date.fromisoformat(d)).days <= 550]
    if past and price:
        _, d, past_pe, past_price = min(past)
        m["pe_then"], m["pe_then_date"] = past_pe, d
        m["price_change_since"] = (price / past_price - 1) * 100
        if pe and pe > 0:
            m["pe_contraction"] = 1.0 if pe < past_pe and price > past_price else 0.0
        elif (m.get("eps") or 0) <= 0 or (m.get("net_margin") or 0) < 0:
            m["pe_contraction"] = 0.0  # no P/E now because there is no profit: no contraction to speak of
    else:
        m["pe_contraction_why"] = "no profitable year end 6-18 months back"


PEERS = 10


def peer_outperformance(m: dict, f: dict, peers: dict[str, float], symbol: str, where: str) -> None:
    """Positive EPS, and EPS growth YoY above the median of the industry's largest companies (Yahoo's figures).

    `peers` are from one market (`where`: "NSE" or "US"); the stock itself is left out, matched on the bare
    symbol so INFY (the ADR) also drops INFY.NS.
    """
    own_sym = symbol.upper().split(".")[0]
    growths = [g for s, g in peers.items() if s.upper().split(".")[0] != own_sym][:PEERS]
    if len(growths) < 3:
        m["eps_vs_peers_why"] = (
            f"only {len(growths)} {where} industry peer{'' if len(growths) == 1 else 's'} with EPS growth on Yahoo"
        )
        return
    median = statistics.median(growths)
    m["peer_eps_growth_median"], m["peer_count"], m["peer_market"] = median * 100, len(growths), where
    own = f.get("earnings_growth_yoy")
    if m.get("eps") is not None and m["eps"] <= 0:
        m["eps_vs_peers"] = 0.0
    elif own is not None:
        m["eps_growth"] = own * 100
        m["eps_vs_peers"] = 1.0 if own > median else 0.0
    else:
        m["eps_vs_peers_why"] = "no EPS growth figure on Yahoo"


def compute(f: dict, today: date | None = None) -> dict:
    q = f.get("quarterly") or []
    a = f.get("annual") or []
    m: dict = {}

    if q:
        latest = q[0]
        prev = q[1] if len(q) > 1 else None
        yoy = year_ago(q, latest)
        m["quarter"] = latest["period"]
        # Yahoo sometimes stops updating a company's quarterly statements.
        m["quarter_stale"] = ((today or date.today()) - date.fromisoformat(latest["period"])).days > STALE_AFTER_DAYS
        m["q_revenue"] = latest.get("revenue")
        m["q_net_income"] = latest.get("net_income")
        m["q_eps"] = latest.get("eps")
        m["rev_qoq"] = pct_change(latest.get("revenue"), (prev or {}).get("revenue"))
        m["rev_yoy"] = pct_change(latest.get("revenue"), (yoy or {}).get("revenue"))
        m["ni_qoq"] = pct_change(latest.get("net_income"), (prev or {}).get("net_income"))
        m["ni_yoy"] = pct_change(latest.get("net_income"), (yoy or {}).get("net_income"))
        # What the changes compare against, so a loss can be described as widening or narrowing.
        m["ni_qoq_base"] = (prev or {}).get("net_income")
        m["ni_yoy_base"] = (yoy or {}).get("net_income")
        m["q_op_margin"] = _margin(latest, "operating_income")
        yoy_margin = _margin(yoy, "operating_income")
        m["op_margin_change_yoy"] = (
            m["q_op_margin"] - yoy_margin if m["q_op_margin"] is not None and yoy_margin is not None else None
        )
        m["yoy_quarter"] = yoy["period"] if yoy else None
        if not m["quarter_stale"]:
            if m["rev_yoy"] is None and f.get("revenue_growth_yoy") is not None:
                m["rev_yoy"] = f["revenue_growth_yoy"] * 100
            if m["ni_yoy"] is None and f.get("earnings_growth_yoy") is not None:
                m["ni_yoy"] = f["earnings_growth_yoy"] * 100

    revs = [r for r in a if r.get("revenue")]
    if len(revs) >= 2:
        years = min(3, len(revs) - 1)
        m["rev_cagr"] = cagr(revs[0]["revenue"], revs[years]["revenue"], years)
        m["rev_cagr_years"] = years
    profits = [r for r in a if r.get("net_income")]
    if len(profits) >= 2:
        years = min(3, len(profits) - 1)
        m["ni_cagr"] = cagr(profits[0]["net_income"], profits[years]["net_income"], years)
        m["ni_cagr_years"] = years
    m["loss_years"] = loss_streak(a)
    if a:
        latest_a = a[0]
        m["fy"] = latest_a["period"]
        m["fy_net_margin"] = _margin(latest_a, "net_income")
        fcfs = [r.get("fcf") for r in a if r.get("fcf") is not None]
        m["fcf_latest"] = fcfs[0] if fcfs else None
        m["fcf_positive_years"] = sum(1 for x in fcfs if x > 0)
        m["fcf_years"] = len(fcfs)
        # Cash conversion: operating cash flow as a share of net profit (needs a profit to be a ratio)
        if latest_a.get("operating_cf") is not None and (latest_a.get("net_income") or 0) > 0:
            m["cash_conversion"] = latest_a["operating_cf"] / latest_a["net_income"] * 100
        if latest_a.get("total_debt") is not None and latest_a.get("equity"):
            m["debt_to_equity"] = latest_a["total_debt"] / latest_a["equity"]

    if m.get("debt_to_equity") is None and f.get("debt_to_equity_pct") is not None:
        m["debt_to_equity"] = f["debt_to_equity_pct"] / 100
    m["roe"] = f["roe"] * 100 if f.get("roe") is not None else None
    net_margin = f.get("net_margin")
    if net_margin == 0 and m.get("q_net_income"):
        net_margin = None  # Yahoo reports 0 for a company with (almost) no revenue; there is no margin to speak of
    m["net_margin"] = net_margin * 100 if net_margin is not None else m.get("fy_net_margin")
    m["pe"] = f.get("pe")
    # PEG: Yahoo's figure, else P/E over 3-year profit CAGR; shrinking profit gives a negative PEG, which fails
    m["peg"] = f.get("peg")
    if m["peg"] is None and (m["pe"] or 0) > 0 and m.get("ni_cagr"):
        m["peg"] = m["pe"] / m["ni_cagr"]
    if f.get("insiders_held") is not None:
        m["promoter_holding"] = f["insiders_held"] * 100
    m["op_margin"] = f["operating_margin"] * 100 if f.get("operating_margin") is not None else m.get("q_op_margin")
    m["current_ratio"] = f.get("current_ratio")
    price, hi, lo = f.get("price"), f.get("high_52w"), f.get("low_52w")
    if price and hi:
        m["from_52w_high"] = (price / hi - 1) * 100
    if price and lo:
        m["from_52w_low"] = (price / lo - 1) * 100
    if price and f.get("prev_close"):
        m["day_change"] = (price / f["prev_close"] - 1) * 100

    # Diluted EPS and BVPS
    eps = f.get("eps")
    if eps is None and price and m.get("pe") and m["pe"] > 0:
        eps = price / m["pe"]
    m["eps"] = eps

    bvps = f.get("bvps")
    if bvps is None and price and f.get("pb") and f["pb"] > 0:
        bvps = price / f["pb"]
    m["bvps"] = bvps

    # Benjamin Graham Number: SQRT(22.5 * EPS * BVPS)
    # Undervalued when Price <= Graham Number, i.e., price_to_graham <= 1.0 (equiv. PE * PB <= 22.5)
    # Yahoo's book value is unreliable when price and statements differ in currency (HDB: P/B 10.4, really ~2.4).
    if f.get("financial_currency") and f.get("currency") and f["financial_currency"] != f["currency"]:
        m["price_to_graham_why"] = f"priced in {f['currency']} but reports in {f['financial_currency']}"
    elif eps is not None and bvps is not None and eps > 0 and bvps > 0:
        graham_num = math.sqrt(22.5 * eps * bvps)
        m["graham_number"] = graham_num
        if price:
            m["price_to_graham"] = price / graham_num
    elif price and m.get("pe") and f.get("pb") and m["pe"] > 0 and f["pb"] > 0:
        ratio = math.sqrt((m["pe"] * f["pb"]) / 22.5)
        m["price_to_graham"] = ratio
        m["graham_number"] = price / ratio if ratio > 0 else None

    # Benjamin Graham Intrinsic Value: (EPS * (8.5 + 2 * g) * 4.4) / Bond_Yield
    if eps is not None and eps > 0:
        g = m.get("ni_cagr")
        if g is None:
            g = m.get("rev_cagr")
        if g is None and f.get("earnings_growth_yoy") is not None:
            g = f["earnings_growth_yoy"] * 100
        g_clamped = max(0.0, min(20.0, g if g is not None else 0.0))
        bond_yield = 7.0 if is_india(f) else 4.4
        iv = (eps * (8.5 + 2 * g_clamped) * 4.4) / bond_yield
        m["graham_iv"] = iv
        m["graham_iv_growth"] = g_clamped
        if price and iv > 0:
            m["price_to_graham_iv"] = price / iv

    pe_history(f, m, today or date.today())

    # Profit CAGR measured against the bar for the company's size: bigger companies grow slower.
    band = cap_band(f.get("market_cap"), f.get("currency"))
    if band:
        m["cap_band"], m["cap_hurdle"] = band, CAP_PROFIT_HURDLE[band]
        if m.get("ni_cagr") is not None:
            m["cap_profit_cagr"] = m["ni_cagr"] - CAP_PROFIT_HURDLE[band]

    return m
