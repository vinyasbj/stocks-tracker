"""Turn what the user typed ("hdfc bank", "RELIANCE", "aapl", "INFY.NS") into securities."""

import csv
import io
import re

from rapidfuzz import fuzz, process

from ..models import Security
from ..store import Store
from . import http

NSE_LIST_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
SEC_LIST_URL = "https://www.sec.gov/files/company_tickers.json"
LIST_TTL = 7 * 24 * 3600

_SUFFIXES = re.compile(
    r"\b(limited|ltd|inc|incorporated|corp|corporation|co|company|plc|holdings?|group|the|class [a-z])\b\.?",
    re.IGNORECASE,
)


def clean_name(name: str) -> str:
    """'Reliance Industries Limited' -> 'Reliance Industries' (for matching and news search)."""
    name = _SUFFIXES.sub(" ", name.replace("&amp;", "&"))
    return re.sub(r"[\s,./]+$", "", re.sub(r"\s+", " ", name)).strip()


def load_universe(store: Store, sec_user_agent: str) -> list[Security]:
    cached = store.cache_get("universe")
    if cached:
        return [Security(**s) for s in cached]
    secs: list[Security] = []
    nse = http.get(NSE_LIST_URL).text
    for row in csv.DictReader(io.StringIO(nse)):
        secs.append(Security(row["SYMBOL"].strip(), "IN", row["NAME OF COMPANY"].strip()))
    us = http.get(SEC_LIST_URL, headers={"User-Agent": sec_user_agent}).json()
    for row in us.values():
        secs.append(Security(row["ticker"].upper(), "US", row["title"], int(row["cik_str"])))
    store.cache_set("universe", [s.__dict__ for s in secs], LIST_TTL)
    return secs


class Resolver:
    def __init__(self, universe: list[Security]):
        self.universe = universe
        self.by_symbol: dict[str, list[Security]] = {}
        for s in universe:
            self.by_symbol.setdefault(s.symbol, []).append(s)
        # SEC lists the same company under several share classes/tickers; match names once each.
        self._names = [clean_name(s.name).lower() for s in universe]

    @staticmethod
    def _split_market(query: str) -> tuple[str, str | None]:
        q = query.strip()
        upper = q.upper()
        if upper.endswith((".NS", ".BO")):
            return q[:-3], "IN"
        if upper.startswith(("NSE:", "BSE:")):
            return q[4:], "IN"
        if upper.startswith(("US:", "NYSE:", "NASDAQ:")):
            return q.split(":", 1)[1], "US"
        return q, None

    def exact(self, query: str) -> list[Security]:
        """Exact symbol matches only (RELIANCE, INFY.NS, US:INFY, BRK-B)."""
        q, market = self._split_market(query)
        return [s for s in self.by_symbol.get(q.upper().replace("-", "."), []) if market in (None, s.market)]

    def resolve(self, query: str, limit: int = 5) -> list[Security]:
        """Best matches first. One result means the match is unambiguous."""
        exact = self.exact(query)
        if exact:
            return exact
        q, market = self._split_market(query)

        pool = range(len(self.universe))
        if market:
            pool = [i for i in pool if self.universe[i].market == market]
        choices = {i: self._names[i] for i in pool}
        hits = process.extract(q.lower(), choices, scorer=fuzz.WRatio, limit=limit * 3, score_cutoff=80)
        seen_names, out = set(), []
        for _, score, i in hits:
            sec = self.universe[i]
            if (sec.market, self._names[i]) in seen_names:
                continue
            seen_names.add((sec.market, self._names[i]))
            out.append((score, sec))
        if not out:
            return []
        # A clear winner (exact name, or well ahead of the next match) needs no follow-up question.
        if out[0][0] >= 99:
            # Exact name matches only; e.g. an NSE listing and its US ADR.
            return [s for score, s in out if score >= 99][:limit]
        if len(out) > 1 and out[0][0] - out[1][0] >= 8:
            return [out[0][1]]
        return [s for _, s in out[:limit]]

    def find(self, symbol: str, market: str) -> Security | None:
        return next((s for s in self.by_symbol.get(symbol, []) if s.market == market), None)
