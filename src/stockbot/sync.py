"""Mirror the Notion portfolio and wishlist into the store; they decide what the alerts cover."""

import logging
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz

from .data import notion
from .data.tickers import Resolver, clean_name
from .models import Holding, Security

log = logging.getLogger(__name__)
Lookup = Callable[[str, str | None], Security | None]  # exact ticker lookup outside the exchange lists


@dataclass
class SyncReport:
    holdings: list[Holding] = field(default_factory=list)
    added: list[Security] = field(default_factory=list)
    removed: list[Security] = field(default_factory=list)
    unmatched: list[tuple[notion.NotionRow, str]] = field(default_factory=list)  # (row, why)


def _with_market(query: str, market: str | None) -> str:
    q = query.strip()
    if not market or ":" in q or q.upper().endswith((".NS", ".BO")):
        return q
    return f"{'NSE' if market == 'IN' else 'US'}:{q}"


NAME_MATCH = 90  # below this a Notion row is reported, not matched


def _name_score(a: str, b: str) -> float:
    # Whole words must match: WRatio's partial matching scores "Star" inside "Startup" as 90.
    return fuzz.token_set_ratio(clean_name(a).lower(), clean_name(b).lower())


def match(resolver: Resolver, row: notion.NotionRow, lookup: Lookup | None = None) -> tuple[Security | None, str]:
    """The listed stock a Notion row means: exact ticker first, then a close name match.

    Returns (security, "") or (None, why). A wrong guess would track the wrong stock, so anything
    uncertain is reported instead.
    """
    cands = resolver.exact(_with_market(row.ticker, row.exchange)) if row.ticker else []
    if len(cands) == 1:
        return cands[0], ""
    if not cands and row.ticker and lookup:
        # ETFs are missing from the NSE and SEC company lists.
        found = lookup(row.ticker, row.exchange)
        if found:
            return found, ""
    if not cands and row.name:
        found = resolver.resolve(_with_market(row.name, row.exchange))
        cands = [c for c in found if _name_score(row.name, c.name) >= NAME_MATCH]
        if not cands:
            closest = f"; closest: {found[0].label}" if found else ""
            return None, f"no listed stock matches{closest}"
    if not cands:
        return None, "no listed stock matches"
    if len(cands) == 1:
        return cands[0], ""
    if row.name:
        # The ticker or name exists in several places (e.g. INFY on NSE and NYSE); let the name decide.
        scored = sorted(((_name_score(row.name, c.name), c) for c in cands), key=lambda x: -x[0])
        if scored[0][0] >= NAME_MATCH and scored[0][0] - scored[1][0] >= 5:
            return scored[0][1], ""
    options = ", ".join(c.label for c in cands[:4])
    return None, f"ambiguous ({options}); set the Exchange column to NSE or US"


def build_holdings(resolver: Resolver, rows: list[notion.NotionRow], lookup: Lookup | None = None) -> SyncReport:
    report = SyncReport()
    for row in rows:
        sec, why = match(resolver, row, lookup)
        if sec is None:
            report.unmatched.append((row, why))
        else:
            if row.list_type == "portfolio":
                pages = (row.page_id,) if row.page_id else ()
                report.holdings.append(Holding(sec, "portfolio", row.qty, row.avg_price, pages, row.conviction))
            else:
                # Theses are read from portfolio rows only, so wishlist rows carry no page.
                report.holdings.append(Holding(sec, "wishlist"))
    return report
