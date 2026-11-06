"""NSE corporate announcements (results, board meetings, orders, ...).

BSE's API sits behind Akamai and rejects scripted clients, so NSE is the only exchange source;
Google News covers anything NSE misses.
"""

from ..models import Item
from . import http

NSE_ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements"

# Routine filings that are not worth an alert.
NOISE = (
    "trading window",
    "newspaper publication",
    "copy of newspaper",
    "loss of share certificate",
    "duplicate share certificate",
    "certificate under sebi (depositories",
    "esop",
    "esos",
    "allotment of securities",
    "analysts/institutional investor meet/con. call updates",
)


GENERIC = {"updates", "general updates", "press release", "general", "others", "clarification - financial results"}


def parse_nse(rows: list[dict], limit: int) -> list[Item]:
    items = []
    for r in rows[:limit]:
        desc = (r.get("desc") or "").strip()
        text = (r.get("attchmntText") or "").strip()
        blob = f"{desc} {text}".lower()
        tags = set()
        if "financial result" in blob:
            tags.add("results")
        if any(n in blob for n in NOISE):
            tags.add("routine")
        title = desc
        if text and (not desc or desc.lower() in GENERIC):
            # "Updates" says nothing; the attachment text says what was filed.
            title = f"{desc}: {text[:140]}" if desc else text[:140]
        items.append(
            Item(
                key=f"nse:{r.get('seq_id') or r.get('dt')}",
                kind="filing",
                title=title,
                url=r.get("attchmntFile") or "",
                source="NSE",
                published=http.parse_date(r.get("sort_date"), "%Y-%m-%d %H:%M:%S"),
                summary=text[:500],
                tags=tags,
            )
        )
    return items


def nse_announcements(symbol: str, limit: int = 20) -> list[Item]:
    resp = http.get(
        NSE_ANNOUNCEMENTS,
        params={"index": "equities", "symbol": symbol},
        headers={"Referer": "https://www.nseindia.com/", "Accept": "application/json"},
    )
    return parse_nse(resp.json(), limit)
