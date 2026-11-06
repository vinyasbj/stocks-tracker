import hashlib
import re
from urllib.parse import quote_plus

import feedparser
import yfinance as yf

from ..models import Item, Security
from . import http
from .tickers import clean_name


# Quote pages and market-wide wrap-ups that merely list the stock.
NOISE = re.compile(
    r"stock price, news, quote|share price (live|today)|top gainers|top losers|stocks to (watch|buy)|"
    r"market (live|wrap)|sensex.*nifty|nifty.*sensex|outlook for the week",
    re.IGNORECASE,
)
TOPIC = "(shares OR stock OR results OR quarter OR profit OR revenue OR order OR deal OR acquisition OR SEBI OR SEC)"


def _title_key(title: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def parse_google_news(xml: bytes, limit: int) -> list[Item]:
    items = []
    for e in feedparser.parse(xml).entries[:limit]:
        title = e.get("title", "")
        source = (e.get("source") or {}).get("title", "")
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        if NOISE.search(title):
            continue
        items.append(
            Item(
                key=f"news:{_title_key(title)}",
                kind="news",
                title=title,
                url=e.get("link", ""),
                source=source or "Google News",
                published=http.parse_date(e.get("published")),
            )
        )
    return items


def google_news(sec: Security, days: int = 7, limit: int = 15) -> list[Item]:
    name = clean_name(sec.name)
    q = f'"{name}" {TOPIC} when:{days}d'
    locale = "hl=en-IN&gl=IN&ceid=IN:en" if sec.market == "IN" else "hl=en-US&gl=US&ceid=US:en"
    resp = http.get(f"https://news.google.com/rss/search?q={quote_plus(q)}&{locale}")
    return parse_google_news(resp.content, limit)


def yahoo_news(sec: Security, limit: int = 10) -> list[Item]:
    items = []
    for n in (yf.Ticker(sec.yf_symbol).news or [])[:limit]:
        c = n.get("content") or n
        title = c.get("title") or ""
        if not title or NOISE.search(title):
            continue
        url = ((c.get("clickThroughUrl") or c.get("canonicalUrl") or {}).get("url")) or ""
        items.append(
            Item(
                key=f"news:{_title_key(title)}",
                kind="news",
                title=title,
                url=url,
                source=(c.get("provider") or {}).get("displayName", "Yahoo Finance"),
                published=http.parse_date(c.get("pubDate"), "%Y-%m-%dT%H:%M:%SZ"),
                summary=(c.get("summary") or "")[:400],
            )
        )
    return items


def company_news(sec: Security, limit: int = 15) -> list[Item]:
    """Yahoo and Google News merged, de-duplicated by headline, newest first."""
    merged: dict[str, Item] = {}
    for fetch in (yahoo_news, google_news):
        try:
            for item in fetch(sec):
                merged.setdefault(item.key, item)
        except Exception:  # one source failing should not hide the other
            continue
    return sort_newest(list(merged.values()))[:limit]


def sort_newest(items: list[Item]) -> list[Item]:
    return sorted(items, key=lambda i: i.published.timestamp() if i.published else 0, reverse=True)
