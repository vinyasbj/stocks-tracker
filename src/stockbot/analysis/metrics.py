"""Derived numbers from the fundamentals dict built by data.market.fetch_fundamentals."""

from datetime import date


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
        if latest_a.get("total_debt") is not None and latest_a.get("equity"):
            m["debt_to_equity"] = latest_a["total_debt"] / latest_a["equity"]

    if m.get("debt_to_equity") is None and f.get("debt_to_equity_pct") is not None:
        m["debt_to_equity"] = f["debt_to_equity_pct"] / 100
    m["roe"] = f["roe"] * 100 if f.get("roe") is not None else None
    m["net_margin"] = f["net_margin"] * 100 if f.get("net_margin") is not None else m.get("fy_net_margin")
    m["pe"] = f.get("pe")
    m["op_margin"] = f["operating_margin"] * 100 if f.get("operating_margin") is not None else m.get("q_op_margin")
    m["current_ratio"] = f.get("current_ratio")
    price, hi, lo = f.get("price"), f.get("high_52w"), f.get("low_52w")
    if price and hi:
        m["from_52w_high"] = (price / hi - 1) * 100
    if price and lo:
        m["from_52w_low"] = (price / lo - 1) * 100
    if price and f.get("prev_close"):
        m["day_change"] = (price / f["prev_close"] - 1) * 100
    return m
