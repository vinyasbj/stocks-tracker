import re

import feedparser

from ..models import Item
from . import http

# name -> (market whose stocks it can affect, feed url)
FEEDS = {
    "SEBI": ("IN", "https://www.sebi.gov.in/sebirss.xml"),
    "RBI press": ("IN", "https://www.rbi.org.in/pressreleases_rss.xml"),
    "RBI notification": ("IN", "https://www.rbi.org.in/notifications_rss.xml"),
    "SEC": ("US", "https://www.sec.gov/news/pressreleases.rss"),
}

# Daily market-operations releases that never move a single stock.
NOISE = re.compile(
    r"auction|money market operations|reserve money|weekly statistical|treasury bill|t-bill|"
    r"state government securities|government stock|sovereign gold bond|cut-off|"
    r"unsc|sanctions list|uapa|foreign exchange turnover",
    re.IGNORECASE,
)


def parse_feed(source: str, xml: bytes, limit: int = 30) -> list[Item]:
    items = []
    for e in feedparser.parse(xml).entries[:limit]:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        # SEBI's feed sometimes prefixes an already absolute link with its own domain.
        if link.count("https://") > 1:
            link = link[link.rindex("https://"):]
        items.append(
            Item(
                key=f"reg:{source}:{link or title}",
                kind="regulator",
                title=title,
                url=link,
                source=source,
                published=http.parse_date(e.get("published"), "%d %b, %Y %z"),
                summary=re.sub(r"<[^>]+>", " ", e.get("summary") or "")[:400].strip(),
                tags={"routine"} if NOISE.search(title) else set(),
            )
        )
    return items


def fetch(source: str) -> list[Item]:
    _, url = FEEDS[source]
    return parse_feed(source, http.get(url).content)
