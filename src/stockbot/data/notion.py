"""Read the portfolio, wishlist and blueprint databases, and stock pages, from Notion; trash sold portfolio rows and add missing logos."""

import re
import time
from dataclasses import dataclass

import httpx

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"  # the first version with data sources

# Column names to look for, compared lower-case with punctuation removed. Overridable in .env.
CANDIDATES = {
    "ticker": ["ticker", "symbol", "nse code", "nse symbol", "ticker symbol", "stock code", "code", "scrip"],
    "name": ["name", "company", "company name", "stock", "stock name"],
    "exchange": ["exchange", "market", "listed on"],
    "qty": ["qty", "quantity", "shares", "units", "no of shares", "holding", "holdings"],
    "avg_price": [
        "avg price", "average price", "avg cost", "average cost", "avg buy price", "buy price",
        "purchase price", "cost price", "avg",
    ],
    "conviction": ["conviction", "conviction level"],
}
CONVICTION_LEVELS = ("HIGH", "MEDIUM", "LOW")


@dataclass(frozen=True)
class NotionRow:
    list_type: str  # "portfolio" | "wishlist"
    ticker: str | None
    name: str | None
    exchange: str | None  # "IN" | "US" | None
    qty: float | None
    avg_price: float | None
    url: str
    page_id: str = ""
    conviction: str | None = None  # "HIGH" | "MEDIUM" | "LOW"
    has_icon: bool = False

    @property
    def label(self) -> str:
        return self.ticker or self.name or self.url


def db_id(link_or_id: str) -> str:
    """The 32-hex id in a Notion database link (or the id itself), dashed.

    In a link the database id is the last one before "?v=<view id>".
    """
    # Dashes are removed, so a title ending in hex letters ("...Feed-<id>") joins the id: keep the last 32.
    hexes = re.findall(r"[0-9a-f]{32,}", link_or_id.split("?")[0].replace("-", "").lower())
    if not hexes:
        raise ValueError(f"No Notion database id in {link_or_id!r}")
    h = hexes[-1][-32:]
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def value(prop: dict):
    """Plain value of a Notion property, for the types a holdings table uses."""
    t = prop.get("type")
    v = prop.get(t)
    if v is None:
        return None
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v).strip() or None
    if t in ("number", "checkbox", "url"):
        return v
    if t in ("select", "status"):
        return v.get("name")
    if t == "multi_select":
        return v[0]["name"] if v else None
    if t == "formula":
        return v.get(v.get("type"))
    if t == "rollup":
        if v.get("type") == "number":
            return v.get("number")
        if v.get("type") == "array" and v.get("array"):
            return value(v["array"][0])
    return None


def find_columns(
    properties: dict, candidates: dict[str, list[str]], overrides: dict[str, str] | None = None
) -> dict[str, str | None]:
    """Map our fields to a database's column names, first candidate that exists wins."""
    by_norm = {_norm(name): name for name in properties}
    cols: dict[str, str | None] = {}
    for field, names in candidates.items():
        if (overrides or {}).get(field):
            cols[field] = overrides[field]
            continue
        cols[field] = next((by_norm[n] for n in names if n in by_norm), None)
    return cols


def pick_columns(properties: dict, overrides: dict[str, str]) -> dict[str, str | None]:
    """Map our fields to the holdings database's column names."""
    cols = find_columns(properties, CANDIDATES, overrides)
    if cols["name"] is None:
        title = next((n for n, p in properties.items() if p.get("type") == "title"), None)
        cols["name"] = title if title != cols["ticker"] else None
    return cols


def _number(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(str(v).replace(",", "").replace("₹", "").replace("$", "").strip())
    except ValueError:
        return None


def conviction_level(v) -> str | None:
    """HIGH / MEDIUM / LOW from a cell or a line of text ("High", "med"), else None."""
    s = str(v or "").strip().upper()
    if s.startswith("MED"):
        return "MEDIUM"
    return next((lvl for lvl in CONVICTION_LEVELS if s.startswith(lvl)), None)


def _exchange(v) -> str | None:
    s = str(v or "").upper()
    if any(x in s for x in ("NSE", "BSE", "INDIA")) or s == "IN":
        return "IN"
    if any(x in s for x in ("NYSE", "NASDAQ", "AMEX", "USA")) or s == "US":
        return "US"
    return None


def parse_rows(
    pages: list[dict], list_type: str, overrides: dict[str, str] | None = None, default_market: str | None = None
) -> list[NotionRow]:
    rows = []
    for page in pages:
        if page.get("in_trash") or page.get("archived"):
            continue
        props = page.get("properties", {})
        cols = pick_columns(props, overrides or {})

        def get(field):
            col = cols.get(field)
            return value(props[col]) if col and col in props else None

        ticker, name = get("ticker"), get("name")
        if not ticker and not name:
            continue  # empty row
        rows.append(
            NotionRow(
                list_type=list_type,
                ticker=str(ticker).strip().upper() if ticker else None,
                name=str(name).strip() if name else None,
                exchange=_exchange(get("exchange")) or _exchange(default_market),
                qty=_number(get("qty")),
                avg_price=_number(get("avg_price")),
                url=page.get("url", ""),
                page_id=page.get("id", ""),
                conviction=conviction_level(get("conviction")),
                has_icon=bool(page.get("icon")),
            )
        )
    return rows


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"}


RETRIES = 3
MAX_WAIT = 30.0


def _send(c: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    """One request, retried when Notion rate-limits (HTTP 429), waiting as long as Retry-After asks."""
    for attempt in range(RETRIES + 1):
        resp = c.request(method, url, **kw)
        if resp.status_code != 429 or attempt == RETRIES:
            return resp
        try:
            wait = float(resp.headers.get("Retry-After", 1))
        except ValueError:
            wait = 1.0
        time.sleep(min(max(wait, 0.0), MAX_WAIT))
    return resp


def query_pages(token: str, database: str) -> list[dict]:
    """Every row of every data source in the database."""
    with httpx.Client(headers=_headers(token), timeout=30) as c:
        resp = _send(c, "GET", f"{API}/databases/{db_id(database)}")
        _raise(resp, database)
        pages = []
        for source in resp.json().get("data_sources", []):
            cursor = None
            while True:
                body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
                r = _send(c, "POST", f"{API}/data_sources/{source['id']}/query", json=body)
                _raise(r, database)
                data = r.json()
                pages.extend(data.get("results", []))
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
        return pages


def update_page(token: str, page_id: str, body: dict) -> None:
    """Change a page: {"in_trash": True} moves it to the trash (restorable for 30 days), {"icon": ...} sets its icon."""
    with httpx.Client(headers=_headers(token), timeout=30) as c:
        _raise(_send(c, "PATCH", f"{API}/pages/{page_id}", json=body), page_id, "page")


def _raise(resp: httpx.Response, database: str, kind: str = "database") -> None:
    if resp.status_code == 404:
        raise PermissionError(
            f"Notion can't find {kind} {database}. Open it in Notion, ⋯ → Connections, and add your integration."
        )
    if resp.status_code == 403:
        raise PermissionError(f"The Notion integration can't change {kind} {database}: give it Update content.")
    if resp.status_code == 401:
        raise PermissionError("Notion rejected NOTION_TOKEN. Copy it again from notion.so/profile/integrations.")
    resp.raise_for_status()


def fetch(
    token: str,
    portfolio_db: str,
    wishlist_db: str,
    overrides: dict[str, str] | None = None,
    markets: dict[str, str] | None = None,
) -> list[NotionRow]:
    """`markets` gives each list's default market ("IN"/"US") for rows without an Exchange column."""
    rows = []
    for list_type, db in (("portfolio", portfolio_db), ("wishlist", wishlist_db)):
        if db:
            rows.extend(parse_rows(query_pages(token, db), list_type, overrides, (markets or {}).get(list_type)))
    return rows


# Blocks whose children are part of the page's text (toggle headings, nested bullets).
NESTED = {"heading_1", "heading_2", "heading_3", "toggle", "bulleted_list_item", "numbered_list_item", "to_do",
          "callout", "quote", "column_list", "column", "synced_block"}


def page_blocks(token: str, page_id: str, depth: int = 3) -> list[dict]:
    """The blocks of a page in reading order, nested children flattened in after their parent."""
    with httpx.Client(headers=_headers(token), timeout=30) as c:
        return _children(c, page_id, depth)


def _children(c: httpx.Client, block_id: str, depth: int) -> list[dict]:
    out: list[dict] = []
    cursor = None
    while True:
        params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        r = _send(c, "GET", f"{API}/blocks/{block_id}/children", params=params)
        _raise(r, block_id, "page")
        data = r.json()
        for block in data.get("results", []):
            out.append(block)
            if block.get("has_children") and block.get("type") in NESTED and depth > 1:
                out.extend(_children(c, block["id"], depth - 1))
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")
