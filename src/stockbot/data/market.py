"""Prices, ratios and financial statements from Yahoo Finance (via yfinance)."""

import math

import yfinance as yf

from ..models import Security
from ..store import Store

FUNDAMENTALS_TTL = 6 * 3600

INFO_FIELDS = {
    "longName": "name",
    "quoteType": "quote_type",  # EQUITY, ETF, MUTUALFUND
    "sector": "sector",
    "industry": "industry",
    "longBusinessSummary": "business_summary",  # what the company does, its segments and regions (text)
    "country": "country",
    "currency": "currency",  # of the price and market cap
    "financialCurrency": "financial_currency",  # of the statements; differs for e.g. INFY.NS (USD)
    "currentPrice": "price",
    "previousClose": "prev_close",
    "marketCap": "market_cap",
    "trailingPE": "pe",
    "forwardPE": "forward_pe",
    "priceToBook": "pb",
    "returnOnEquity": "roe",
    "debtToEquity": "debt_to_equity_pct",  # Yahoo reports this as a percentage
    "currentRatio": "current_ratio",
    "profitMargins": "net_margin",
    "operatingMargins": "operating_margin",
    "dividendYield": "dividend_yield",  # already in percent
    "fiftyTwoWeekHigh": "high_52w",
    "fiftyTwoWeekLow": "low_52w",
    "beta": "beta",
    "freeCashflow": "free_cashflow",
    # Yahoo's own YoY growth for its most recent quarter; used when the statements skip a quarter.
    "revenueGrowth": "revenue_growth_yoy",
    "earningsGrowth": "earnings_growth_yoy",
    # ETFs and funds. Yahoo mixes units: the expense ratio and YTD return are already percentages,
    # the 3y/5y average returns and the yield are fractions.
    "category": "category",
    "fundFamily": "fund_family",
    "totalAssets": "total_assets",
    "netExpenseRatio": "expense_ratio_pct",
    "ytdReturn": "ytd_return_pct",
    "threeYearAverageReturn": "return_3y",
    "fiveYearAverageReturn": "return_5y",
    "yield": "fund_yield",
}


TEXT_FIELDS = {"name", "quote_type", "category", "fund_family", "sector", "industry", "currency", "financial_currency", "business_summary", "country"}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _rows(df, fields: dict[str, str], limit: int) -> list[dict]:
    """Statement DataFrame (rows = line items, cols = period ends) -> newest-first dicts."""
    if df is None or df.empty:
        return []
    out = []
    for col in list(df.columns)[:limit]:
        row = {"period": col.strftime("%Y-%m-%d")}
        for src, dst in fields.items():
            row[dst] = _num(df.at[src, col]) if src in df.index else None
        out.append(row)
    return out


def fetch_fundamentals(sec: Security, store: Store) -> dict:
    key = f"fund:{sec.market}:{sec.symbol}"
    cached = store.cache_get(key)
    if cached:
        return cached
    t = yf.Ticker(sec.yf_symbol)
    info = t.info or {}
    data = {dst: info.get(src) for src, dst in INFO_FIELDS.items()}
    for k, v in data.items():
        if k not in TEXT_FIELDS:
            data[k] = _num(v)
    data["name"] = data["name"] or sec.name
    if data["price"] is None:
        data["price"] = _num(info.get("regularMarketPrice"))

    data["quarterly"] = _rows(
        t.quarterly_income_stmt,
        {"Total Revenue": "revenue", "Net Income": "net_income", "Operating Income": "operating_income", "Diluted EPS": "eps"},
        limit=8,
    )
    annual = _rows(
        t.income_stmt,
        {"Total Revenue": "revenue", "Net Income": "net_income", "Operating Income": "operating_income"},
        limit=5,
    )
    balance = {r["period"]: r for r in _rows(t.balance_sheet, {"Total Debt": "total_debt", "Stockholders Equity": "equity"}, 5)}
    cash = {r["period"]: r for r in _rows(t.cashflow, {"Free Cash Flow": "fcf", "Operating Cash Flow": "operating_cf"}, 5)}
    for row in annual:
        row.update({k: v for k, v in balance.get(row["period"], {}).items() if k != "period"})
        row.update({k: v for k, v in cash.get(row["period"], {}).items() if k != "period"})
    data["annual"] = annual

    if not data["quarterly"] and not annual and data["price"] is None:
        raise LookupError(f"Yahoo Finance has no data for {sec.yf_symbol}")
    store.cache_set(key, data, FUNDAMENTALS_TTL)
    return data


def fetch_quote(sec: Security) -> dict:
    """Fresh price, previous close and 52-week high (from closes), for /portfolio and the digest."""
    hist = yf.Ticker(sec.yf_symbol).history(period="1y")["Close"].dropna()
    if hist.empty:
        raise LookupError(f"No price history for {sec.yf_symbol}")
    last = float(hist.iloc[-1])
    prev = float(hist.iloc[-2]) if len(hist) > 1 else None
    return {
        "price": last,
        "prev": prev,
        "change_pct": (last / prev - 1) * 100 if prev else None,
        "high_52w": float(hist.max()),
    }


LISTED_TYPES = ("EQUITY", "ETF", "MUTUALFUND")


FUND_TYPES = ("ETF", "MUTUALFUND")


def lookup_symbol(symbol: str, market: str | None, types: tuple[str, ...] = LISTED_TYPES) -> Security | None:
    """Exact ticker lookup on Yahoo, for listings the NSE and SEC lists leave out (ETFs).

    Never fuzzy: the symbol must exist as typed.
    """
    sym = symbol.strip().upper()
    if sym.endswith((".NS", ".BO")):
        sym, market = sym[:-3], "IN"
    for mkt in [market] if market else ["US", "IN"]:
        cand = Security(sym, mkt, sym)
        try:
            info = yf.Ticker(cand.yf_symbol).info or {}
        except Exception:
            continue
        price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("navPrice")
        if info.get("quoteType") in types and price:
            return Security(sym, mkt, info.get("longName") or info.get("shortName") or sym)
    return None
