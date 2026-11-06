"""Everything the bot can do, independent of Telegram (the CLI uses it too)."""

import asyncio
import logging
import re

from html import escape
from typing import Awaitable, Callable

from .alerts.monitor import Monitor
from .analysis import report
from .config import Settings
from .data import market, notion, tickers
from .llm.client import LLM
from .llm.prompts import QA_SYSTEM, RESULTS_SYSTEM, SUMMARY_SYSTEM, INTENT_SYSTEM, Intent
from .models import Security
from .store import Store
from .sync import SyncReport, build_holdings

log = logging.getLogger(__name__)
UNMATCHED_TTL = 30 * 24 * 3600
Send = Callable[[int, str], Awaitable[None]]


def plain_text(text: str) -> str:
    """Small models add markdown despite being told not to; Telegram would show the asterisks."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)


class Engine:
    def __init__(self, settings: Settings, store: Store | None = None):
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.llm = LLM(settings.ollama_url, settings.ollama_model, settings.ollama_num_ctx)
        self.monitor = Monitor(settings, self.store, self.llm)
        self._resolver: tickers.Resolver | None = None

    async def resolver(self) -> tickers.Resolver:
        if self._resolver is None:
            universe = await asyncio.to_thread(tickers.load_universe, self.store, self.settings.sec_user_agent)
            self._resolver = tickers.Resolver(universe)
        return self._resolver

    async def resolve(self, query: str) -> list[Security]:
        return (await self.resolver()).resolve(query)

    async def research(self, sec: Security, with_updates: bool = True) -> report.Research:
        return await asyncio.to_thread(report.gather, sec, self.store, self.settings, with_updates)

    async def _write(self, system: str, r: report.Research, ask: str) -> str:
        try:
            return plain_text(await self.llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": f"DATA:\n{report.data_block(r)}\n\n{ask}"}]
            ))
        except Exception as e:
            log.warning("LLM failed: %s", e)
            return f"(The local model is unavailable: {e}. Is `ollama serve` running?)"

    async def summary(self, r: report.Research) -> str:
        return await self._write(SUMMARY_SYSTEM, r, "Write the summary.")

    async def results_summary(self, r: report.Research) -> str:
        return await self._write(RESULTS_SYSTEM, r, "Explain the latest quarterly results.")

    async def news(self, sec: Security, limit: int = 10) -> str:
        items = [i for i in await asyncio.to_thread(report.updates, sec, self.settings) if "routine" not in i.tags][:limit]
        impacts = await report.classify(self.llm, sec, items)
        return report.render_updates(sec, items, impacts)

    async def parse_intent(self, text: str) -> Intent:
        try:
            return await self.llm.structured(
                [{"role": "system", "content": INTENT_SYSTEM}, {"role": "user", "content": text}], Intent
            )
        except Exception as e:
            log.warning("intent parse failed: %s", e)
            return Intent(company=None, intent="other")

    async def answer(self, chat_id: int, sec: Security, question: str) -> str:
        r = await self.research(sec)
        system = f"{QA_SYSTEM}\n\nDATA:\n{report.data_block(r)}"
        history = self.store.history(chat_id)
        try:
            text = await self.llm.chat(
                [{"role": "system", "content": system}, *history, {"role": "user", "content": question}]
            )
        except Exception as e:
            return f"(The local model is unavailable: {e}. Is `ollama serve` running?)"
        text = plain_text(text)
        self.store.add_turn(chat_id, "user", f"[{sec.symbol}] {question}")
        self.store.add_turn(chat_id, "assistant", text)
        return text

    # --- portfolio and wishlist (Notion) ---

    async def sync(self) -> SyncReport:
        s = self.settings
        if not s.notion_token or not (s.notion_portfolio_db or s.notion_wishlist_db):
            raise RuntimeError(
                "Notion is not set up: fill NOTION_TOKEN, NOTION_PORTFOLIO_DB and NOTION_WISHLIST_DB in .env."
            )
        markets = {"portfolio": s.notion_portfolio_market, "wishlist": s.notion_wishlist_market}
        rows = await asyncio.to_thread(
            notion.fetch, s.notion_token, s.notion_portfolio_db, s.notion_wishlist_db, s.notion_columns, markets
        )
        rep = await asyncio.to_thread(build_holdings, await self.resolver(), rows, self._lookup)
        rep.added, rep.removed = self.store.replace_holdings(rep.holdings)
        if rep.added or rep.removed:
            log.info("notion sync: +%s -%s", [x.symbol for x in rep.added], [x.symbol for x in rep.removed])
        return rep

    def _lookup(self, symbol: str, mkt: str | None) -> Security | None:
        """market.lookup_symbol, cached for a week (misses for a day) so syncs stay fast."""
        key = f"lookup:{mkt}:{symbol.upper()}"
        hit = self.store.cache_get(key)
        if hit is not None:
            return Security(**hit) if hit else None
        sec = market.lookup_symbol(symbol, mkt)
        self.store.cache_set(key, sec.__dict__ if sec else {}, 7 * 24 * 3600 if sec else 24 * 3600)
        return sec

    async def check(self, chats: list[int], send: Send) -> int:
        """Sync Notion, then alert on anything new for the portfolio and wishlist."""
        sent = 0
        try:
            rep = await self.sync()
            sent += await self._warn_unmatched(rep, chats, send)
        except Exception as e:
            log.warning("Notion sync failed, using the last synced lists: %s", e)
        return sent + await self.monitor.run(self.store.holdings(), chats, send)

    async def _warn_unmatched(self, rep: SyncReport, chats: list[int], send: Send) -> int:
        """Tell the user once about Notion rows the bot cannot map to a listed stock."""
        fresh = []
        for row, why in rep.unmatched:
            key = f"unmatched:{row.url}:{row.label}"
            if self.store.cache_get(key) is None:
                self.store.cache_set(key, True, UNMATCHED_TTL)
                fresh.append(f"• {escape(row.label)} ({row.list_type}): {escape(why)}")
        if not fresh:
            return 0
        msg = "⚠️ <b>Notion rows I can't track</b>\n" + "\n".join(fresh) + "\n\nFix the ticker in Notion, then /sync."
        for chat in chats:
            await send(chat, msg)
        return len(chats)
