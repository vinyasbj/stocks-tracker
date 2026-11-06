"""Polls filings, news and regulator feeds for the portfolio and wishlist and pushes what is new."""

import asyncio
import logging
import re
from dataclasses import dataclass
from html import escape
from typing import Awaitable, Callable

from ..analysis import report
from ..analysis.signals import is_financial
from ..config import Settings
from ..data import market, regulators
from ..data.tickers import clean_name
from ..llm.client import LLM
from ..llm.prompts import REGULATOR_SYSTEM, RegulatorImpact
from ..models import Holding, Item, Security
from ..store import Store

log = logging.getLogger(__name__)
MAX_NEW_PER_STOCK = 8
MAX_REG_PER_FEED = 10

Send = Callable[[int, str], Awaitable[None]]


def stock_scope(sec: Security) -> str:
    return f"stock:{sec.market}:{sec.symbol}"


def new_items(store: Store, scope: str, items: list[Item]) -> list[Item] | None:
    """Unseen, non-routine items; marks everything seen.

    None on the first pass for a scope: that pass only records a baseline, so a stock newly added
    in Notion does not replay its history.
    """
    keys = [i.key for i in items]
    if not store.has_scope(scope):
        store.mark_seen(scope, keys)
        return None
    fresh = store.unseen(scope, keys)
    store.mark_seen(scope, keys)
    return [i for i in items if i.key in fresh and "routine" not in i.tags]


def mentions(item: Item, sec: Security) -> bool:
    text = f"{item.title} {item.summary}".lower()
    name = clean_name(sec.name).lower()
    if len(name) >= 4 and name in text:
        return True
    return len(sec.symbol) >= 3 and re.search(rf"\b{re.escape(sec.symbol.lower())}\b", text) is not None


def regulator_candidates(item: Item, stocks: list[tuple[Holding, dict]]) -> list[tuple[Holding, dict]]:
    """Cheap pre-filter before asking the LLM: named companies, plus financials for financial regulators."""
    out = []
    for h, fund in stocks:
        if mentions(item, h.sec) or (item.source in ("RBI press", "RBI notification", "SEBI") and is_financial(fund)):
            out.append((h, fund))
    return out


@dataclass
class Monitor:
    settings: Settings
    store: Store
    llm: LLM

    def __post_init__(self):
        self.lock = asyncio.Lock()

    async def run(self, holdings: list[Holding], chats: list[int], send: Send) -> int:
        """One polling pass over the portfolio and wishlist. Returns the number of messages sent."""
        async with self.lock:
            sent = 0
            for h in holdings:
                sent += await self._stock(h, chats, send)
            sent += await self._regulators(holdings, chats, send)
            return sent

    async def _stock(self, h: Holding, chats: list[int], send: Send) -> int:
        sec = h.sec
        items = await asyncio.to_thread(report.updates, sec, self.settings)
        new = new_items(self.store, stock_scope(sec), items)
        if not new:
            return 0
        new = new[:MAX_NEW_PER_STOCK]
        impacts = await report.classify(self.llm, sec, new)
        # Filings always go out; news only when the model does not rate it low.
        keep = [i for i in new if i.kind == "filing" or (impacts.get(i.key) and impacts[i.key].importance != "low")]
        if not keep:
            return 0
        body = "\n".join(report.item_line(i, impacts.get(i.key)) for i in keep)
        msg = report.clip(f"🔔 {h.icon} <b>New for {escape(sec.label)}</b> ({h.list_type})\n\n{body}")
        for chat in chats:
            await send(chat, msg)
        return len(chats)

    async def _regulators(self, holdings: list[Holding], chats: list[int], send: Send) -> int:
        sent = 0
        for source, (mkt, _) in regulators.FEEDS.items():
            stocks = [h for h in holdings if h.sec.market == mkt]
            try:
                items = await asyncio.to_thread(regulators.fetch, source)
            except Exception as e:
                log.warning("regulator feed %s: %s", source, e)
                continue
            new = new_items(self.store, f"reg:{source}", items)
            if not new or not stocks:
                continue
            with_fund = []
            for h in stocks:
                try:
                    with_fund.append((h, await asyncio.to_thread(market.fetch_fundamentals, h.sec, self.store)))
                except Exception:
                    with_fund.append((h, {}))
            for item in new[:MAX_REG_PER_FEED]:
                sent += await self._regulator_item(item, with_fund, chats, send)
        return sent

    async def _regulator_item(self, item: Item, with_fund, chats: list[int], send: Send) -> int:
        cands = regulator_candidates(item, with_fund)
        if not cands:
            return 0
        listing = "\n".join(
            f"- {h.sec.symbol}: {h.sec.name}; sector {f.get('sector') or 'n/a'}; industry {f.get('industry') or 'n/a'}"
            for h, f in cands
        )
        try:
            verdict = await self.llm.structured(
                [
                    {"role": "system", "content": REGULATOR_SYSTEM},
                    {"role": "user", "content": f"RELEASE ({item.source}): {item.title}\n{item.summary}\n\nSTOCKS:\n{listing}"},
                ],
                RegulatorImpact,
            )
        except Exception as e:
            log.warning("regulator relevance failed for %r: %s", item.title, e)
            return 0
        by_symbol = {h.sec.symbol: h for h, _ in cands}
        lines = []
        for hit in verdict.affected:
            h = by_symbol.get(hit.symbol.upper())
            if h:
                lines.append(f"{report.IMPACT_ICON[hit.impact]} {h.icon} {escape(h.sec.symbol)}: {escape(hit.reason)}")
        if not lines:
            return 0
        link = f'<a href="{escape(item.url, quote=True)}">{escape(item.title)}</a>' if item.url else escape(item.title)
        for chat in chats:
            await send(chat, f"🏛️ <b>{escape(item.source)}</b>: {link}\n\n" + "\n".join(lines))
        return len(chats)
