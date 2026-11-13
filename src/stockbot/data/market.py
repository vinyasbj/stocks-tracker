"""Prices, ratios and financial statements from Yahoo Finance (via yfinance)."""

import math
from urllib.parse import urlparse

import pandas as pd

import yfinance as yf
from yfinance import EquityQuery as Q
from yfinance.const import EQUITY_SCREENER_EQ_MAP

from ..models import Security
from ..store import Store

FUNDAMENTALS_TTL = 6 * 3600
PEERS_TTL = 24 * 3600
PEERS = 10  # the industry's largest companies by market cap, in the stock's own market
PEER_SCAN = 25  # screener rows to look through for them (some are preferred shares, ADRs, or have no figure)
NSE_EXCHANGES = ["NSI"]  # NSE only, so a company listed on both NSE and BSE counts once
US_EXCHANGES = ["NYQ", "NMS", "NGM", "NCM", "ASE"]  # NYSE, Nasdaq tiers, NYSE American; no OTC
SCREENER_INDUSTRIES = {i for names in EQUITY_SCREENER_EQ_MAP["industry"].values() for i in names}

INFO_FIELDS = {
    "longName": "name",
    "website": "website",
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
    "trailingEps": "eps",
    "bookValue": "bvps",
    "priceToBook": "pb",
    "trailingPegRatio": "peg",
    "heldPercentInsiders": "insiders_held",  # fraction; for Indian companies this is the promoter holding
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


TEXT_FIELDS = {"name", "website", "quote_type", "category", "fund_family", "sector", "industry", "currency", "financial_currency", "business_summary", "country"}


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
        {"Total Revenue": "revenue", "Net Income": "net_income", "Operating Income": "operating_income", "Diluted EPS": "eps"},
        limit=5,
    )
    balance = {r["period"]: r for r in _rows(t.balance_sheet, {"Total Debt": "total_debt", "Stockholders Equity": "equity"}, 5)}
    cash = {r["period"]: r for r in _rows(t.cashflow, {"Free Cash Flow": "fcf", "Operating Cash Flow": "operating_cf"}, 5)}
    for row in annual:
        row.update({k: v for k, v in balance.get(row["period"], {}).items() if k != "period"})
        row.update({k: v for k, v in cash.get(row["period"], {}).items() if k != "period"})
    data["annual"] = annual
    _add_year_end_prices(t, annual)

    if not data["quarterly"] and not annual and data["price"] is None:
        raise LookupError(f"Yahoo Finance has no data for {sec.yf_symbol}")
    store.cache_set(key, data, FUNDAMENTALS_TTL)
    return data


def _close_on(closes, day: str):
    """The last close on or before `day` (YYYY-MM-DD), within a week; None when history doesn't reach it."""
    end = pd.Timestamp(day)
    if closes.index.tz is not None:
        end = end.tz_localize(closes.index.tz)
    before = closes[closes.index <= end]
    if before.empty or (end - before.index[-1]).days > 7:
        return None
    return _num(before.iloc[-1])


def _add_year_end_prices(t, annual: list[dict]) -> None:
    """The share price at each fiscal year end, so past P/Es can be rebuilt from that year's EPS."""
    if not annual:
        return
    try:
        closes = t.history(period="5y", auto_adjust=False)["Close"].dropna()
    except Exception:
        return
    if closes.empty:
        return
    for row in annual:
        row["price_end"] = _close_on(closes, row["period"])


def peer_market(sec: Security, fundamentals: dict) -> str:
    """Where a company's peers come from: "IN" for an Indian company (even its US ADR, like INFY), else "US"."""
    return "IN" if sec.market == "IN" or fundamentals.get("country") == "India" else "US"


def _peer_query(industry: str, market: str) -> Q:
    exchanges = NSE_EXCHANGES if market == "IN" else US_EXCHANGES
    return Q("and", [
        Q("eq", ["region", market.lower()]),
        Q("eq", ["industry", industry]),
        Q("is-in", ["exchange", *exchanges]),
    ])


def screener_industry(industry: str | None) -> str | None:
    """Yahoo's screener spells "Banks - Regional" as "Banks—Regional"; None for an industry it doesn't list."""
    name = (industry or "").replace(" - ", "—")
    return name if name in SCREENER_INDUSTRIES else None


def peer_eps_growth(industry: str, market: str, store: Store) -> dict[str, float]:
    """{symbol: EPS growth YoY as a fraction} for the largest companies in the industry in one market.

    "IN": NSE listings only. "US": NYSE and Nasdaq listings, leaving out over-the-counter quotes, preferred
    shares and Indian companies listed there as ADRs (HDB, IBN, INFY, WIT), so the two markets never mix.
    """
    key = f"peers:{market}:{industry}"
    cached = store.cache_get(key)
    if cached is not None:
        return cached
    quotes = yf.screen(_peer_query(industry, market), sortField="intradaymarketcap", sortAsc=False, size=PEER_SCAN)
    out: dict[str, float] = {}
    failed = False
    for q in quotes.get("quotes", []):
        sym = q.get("symbol") or ""
        if len(out) > PEERS:  # one spare, in case the stock itself is among them
            break
        if q.get("quoteType") != "EQUITY" or "-" in sym:  # "USB-PH" is a preferred share
            continue
        try:
            info = yf.Ticker(sym).info or {}
        except Exception:
            failed = True  # e.g. rate-limited: the list is incomplete, so don't keep it for a day
            continue
        if market == "US" and info.get("country") == "India":
            continue
        g = _num(info.get("earningsGrowth"))
        if g is not None:
            out[sym] = g
    if not failed:
        store.cache_set(key, out, PEERS_TTL)
    return out


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
# shortcut: only common fund families, add one when a new ETF gets no logo.
FUND_SITES = {
    "vanguard": "vanguard.com", "proshares": "proshares.com", "invesco": "invesco.com", "ishares": "ishares.com",
    "global x": "globalxetfs.com", "roundhill": "roundhillinvestments.com", "state street": "ssga.com",
    "schwab": "schwab.com",
}


def logo_url(fundamentals: dict) -> str | None:
    """Google's favicon for the company's (or an ETF's fund family's) website, as the Notion page icons use."""
    domain = (urlparse(fundamentals.get("website") or "").hostname or "").removeprefix("www.")
    if not domain:
        family = (fundamentals.get("fund_family") or "").lower()
        domain = next((d for name, d in FUND_SITES.items() if name in family), "")
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128" if domain else None


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
