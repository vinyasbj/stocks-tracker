"""Everything the bot can do, independent of Telegram (the CLI uses it too)."""

import asyncio
import hashlib
import logging
import re

from html import escape
from typing import Awaitable, Callable

from .alerts.monitor import Monitor
from .analysis import blueprint, metrics, report, signals, thesis, verdict
from .analysis.conviction import (
    BlueprintStatus, Conviction, LockResult, Profile, ReportEntry, ReportResult, explain_data,
)
from .config import Settings
from .data import market, notion, tickers
from .llm.client import LLM
from .llm.prompts import (
    CONVICTION_SYSTEM, INTENT_SYSTEM, PROFILE_SYSTEM, QA_SYSTEM, RESULTS_SYSTEM, SUMMARY_SYSTEM, Intent,
)
from .models import Holding, Security
from .store import Store
from .sync import SyncReport, build_holdings

log = logging.getLogger(__name__)
UNMATCHED_TTL = 30 * 24 * 3600
PROFILE_TTL = 30 * 24 * 3600
WHY_TTL = 7 * 24 * 3600
WHY_MAX_TOKENS = 150  # about 100 words; the reply is cut to 3 sentences anyway, and writing costs ~6 tokens/s
REPORT_CONCURRENCY = 4
EXCERPT = 300
Send = Callable[[int, str], Awaitable[None]]


# "QQQM", "us:qqqm", "NSE:NIFTYBEES", "NIFTYBEES.NS": a ticker as typed, never a company name in words.
TICKER_ONLY = re.compile(r"(?:(?P<prefix>NSE|BSE|US):)?(?P<symbol>[A-Za-z0-9&\-]{1,15}(?:\.(?:NS|BO))?)", re.IGNORECASE)


def excerpt(text: str, limit: int = EXCERPT) -> str:
    """The start of a long text, cut at a sentence or word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = cut.rfind(". ")
    return cut[: end + 1] if end > limit // 2 else cut[: cut.rfind(" ")] + "…"


def invents_numbers(text: str, source: str) -> bool:
    """True when `text` has a number that `source` does not: the model made it up."""
    return any(n not in source for n in re.findall(r"\d+(?:[.,]\d+)?", text))


def first_sentences(text: str, n: int = 3) -> str:
    """The first `n` sentences ("24.3%" and "U.S." don't end one: a sentence ends before a capital)."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text.strip())
    return " ".join(parts[:n])


def plain_text(text: str) -> str:
    """Small models add markdown despite being told not to; Telegram would show the asterisks."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)


class Engine:
    def __init__(self, settings: Settings, store: Store | None = None):
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.llm = LLM(settings.ollama_url, settings.ollama_model, settings.ollama_num_ctx, settings.ollama_timeout)
        self.monitor = Monitor(settings, self.store, self.llm)
        self._resolver: tickers.Resolver | None = None

    async def resolver(self) -> tickers.Resolver:
        if self._resolver is None:
            universe = await asyncio.to_thread(tickers.load_universe, self.store, self.settings.sec_user_agent)
            self._resolver = tickers.Resolver(universe)
        return self._resolver

    async def resolve(self, query: str) -> list[Security]:
        """Stocks from the NSE and SEC lists; failing that, an exact ticker on Yahoo (ETFs and funds aren't listed)."""
        found = (await self.resolver()).resolve(query)
        m = TICKER_ONLY.fullmatch(query.strip())
        if not m:
            return found
        mkt = {"NSE": "IN", "BSE": "IN", "US": "US"}.get((m["prefix"] or "").upper())
        if not found:
            sec = await asyncio.to_thread(self._lookup, m["symbol"], mkt)
            return [sec] if sec else []
        symbol = re.sub(r"\.(NS|BO)$", "", m["symbol"], flags=re.IGNORECASE).upper()
        if any(s.symbol == symbol for s in found):
            return found
        # Only fuzzy name matches ("URA" → "Accuracy Shipping"): an ETF or fund with exactly that ticker wins.
        fund = await asyncio.to_thread(self._lookup, m["symbol"], mkt, market.FUND_TYPES)
        return [fund] if fund else found

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

    async def sync(self, write_notion: bool = True) -> SyncReport:
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
        if write_notion:  # trash sold rows, add missing logos
            for row in rep.sold:
                try:
                    await asyncio.to_thread(notion.update_page, s.notion_token, row.page_id, {"in_trash": True})
                    log.info("notion sync: deleted sold row %s", row.label)
                except Exception as e:
                    log.warning("couldn't delete sold row %s from Notion: %s", row.label, e)
            for page, sec in rep.need_logo:
                try:
                    url = market.logo_url(await asyncio.to_thread(market.fetch_fundamentals, sec, self.store))
                    if url:
                        icon = {"icon": {"type": "external", "external": {"url": url}}}
                        await asyncio.to_thread(notion.update_page, s.notion_token, page, icon)
                except Exception as e:
                    log.warning("couldn't set the Notion logo for %s: %s", sec.symbol, e)
        rep.added, rep.removed = self.store.replace_holdings(rep.holdings)
        if rep.added or rep.removed:
            log.info("notion sync: +%s -%s", [x.symbol for x in rep.added], [x.symbol for x in rep.removed])
        return rep

    def _lookup(self, symbol: str, mkt: str | None, types: tuple[str, ...] | None = None) -> Security | None:
        """market.lookup_symbol, cached for a week (misses for a day) so syncs stay fast."""
        key = f"lookup:{mkt}:{symbol.upper()}" + (f":{'|'.join(types)}" if types else "")
        hit = self.store.cache_get(key)
        if hit is not None:
            return Security(**hit) if hit else None
        sec = market.lookup_symbol(symbol, mkt, types) if types else market.lookup_symbol(symbol, mkt)
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

    # --- conviction blueprint ---

    async def blueprint_status(self) -> BlueprintStatus:
        """The locked blueprint, and what Notion holds now (with the difference), when it can be read."""
        st = BlueprintStatus(self.store.locked_blueprint())
        s = self.settings
        if not s.notion_token or not s.notion_blueprint_db:
            st.error = "NOTION_BLUEPRINT_DB is not set in .env"
            return st
        try:
            pages = await asyncio.to_thread(notion.query_pages, s.notion_token, s.notion_blueprint_db)
        except Exception as e:
            log.warning("blueprint read failed: %s", e)
            st.error = f"couldn't read the blueprint from Notion: {e}"
            return st
        st.parsed = blueprint.parse_rules(pages)
        if st.locked:
            st.diff = blueprint.diff(st.locked.blueprint, st.parsed.blueprint)
        return st

    async def lock_blueprint(self) -> LockResult:
        """Store what Notion holds as the next version, but only when it is valid and has changed."""
        st = await self.blueprint_status()
        res = LockResult(st)
        if st.error or st.parsed is None or st.parsed.problems or not st.parsed.blueprint.rules:
            return res
        if st.locked and st.locked.blueprint.fingerprint() == st.parsed.blueprint.fingerprint():
            res.unchanged = True
            return res
        res.version = self.store.lock_blueprint(st.parsed.blueprint)
        log.info("blueprint v%d locked (%d rules)", res.version, len(st.parsed.blueprint.rules))
        return res

    def _thesis(self, holding: Holding | None) -> tuple[thesis.Thesis | None, str | None, str]:
        """(thesis, note to show, note for unconfirmed judgment rules) from the holding's portfolio pages."""
        if holding is None or holding.list_type != "portfolio":
            return None, None, "not in your portfolio"
        failed = None
        for page in holding.pages:
            try:
                th = thesis.parse(page, notion.page_blocks(self.settings.notion_token, page))
            except Exception as e:
                log.warning("thesis page %s for %s: %s", page, holding.sec.symbol, e)
                failed = f"Your thesis could not be read from Notion: {e}"
                continue
            if not th.empty:
                return th, None, ""
        if failed:
            return None, failed, "thesis could not be read"
        return None, None, "no thesis in Notion"

    async def _add_peers(self, sec: Security, r: report.Research) -> None:
        """EPS growth against the industry's largest companies in the same market; unknown when Yahoo can't say."""
        f = r.fundamentals
        if signals.is_fund(f):
            return
        industry = market.screener_industry(f.get("industry"))
        if industry is None:
            r.metrics["eps_vs_peers_why"] = f"Yahoo lists no peers for industry {f.get('industry')!r}"
            return
        where = market.peer_market(sec, f)
        try:
            peers = await asyncio.to_thread(market.peer_eps_growth, industry, where, self.store)
        except Exception as e:
            log.warning("peers for %s (%s, %s): %s", sec.symbol, industry, where, e)
            r.metrics["eps_vs_peers_why"] = "couldn't fetch its industry peers"
            return
        metrics.peer_outperformance(r.metrics, f, peers, sec.symbol, "NSE" if where == "IN" else "US")

    async def conviction(self, sec: Security, with_updates: bool = True, check_notion: bool = True) -> Conviction | None:
        """The stock judged by the locked blueprint; None when nothing is locked yet."""
        locked = self.store.locked_blueprint()
        if locked is None:
            return None
        holding = self.store.holding(sec)
        held = holding is not None and holding.list_type == "portfolio"
        r = await self.research(sec, with_updates)
        if any(rule.metric == "eps_vs_peers" for rule in locked.blueprint.rules):
            await self._add_peers(sec, r)
        th, note, check_note = await asyncio.to_thread(self._thesis, holding)
        checks = verdict.evaluate(
            locked.blueprint.rules, r.metrics, r.fundamentals, th, check_note or "no thesis in Notion", held=held
        )
        v = verdict.decide(checks, held, locked.blueprint.pass_bar)
        level = (holding.conviction if holding else None) or (th.conviction if th else None)
        fund = signals.is_fund(r.fundamentals)  # shown, not judged: no verdict, so nothing to disagree with
        c = Conviction(sec, r, holding, locked, v, th, note, level, None if fund else verdict.mismatch(level, v))
        if check_notion and not fund:
            c.unlocked = (await self.blueprint_status()).diff
        return c

    async def conviction_report(self) -> ReportResult:
        """Every portfolio holding judged by the locked blueprint. One stock failing doesn't stop the rest."""
        locked = self.store.locked_blueprint()
        rep = ReportResult(locked)
        if locked is None:
            return rep
        try:
            await self.sync(write_notion=False)  # rows added or removed since the last sync; the report never writes
        except Exception as e:
            log.warning("report: Notion sync failed, using the last synced list: %s", e)
            rep.sync_error = str(e) or type(e).__name__
        gate = asyncio.Semaphore(REPORT_CONCURRENCY)

        async def one(h: Holding) -> ReportEntry:
            async with gate:
                try:
                    return ReportEntry(h, await self.conviction(h.sec, with_updates=False, check_notion=False))
                except Exception as e:
                    log.warning("report: %s failed: %s", h.sec.symbol, e)
                    return ReportEntry(h, error=str(e) or type(e).__name__)

        holdings = [h for h in self.store.holdings() if h.list_type == "portfolio"]
        rep.entries = list(await asyncio.gather(*(one(h) for h in holdings)))
        rep.unlocked = (await self.blueprint_status()).diff
        return rep

    async def business_profile(self, sec: Security, r: report.Research) -> Profile:
        """How and where the company makes money, in at most 3 lines from Yahoo's description (no numbers)."""
        desc = (r.fundamentals.get("business_summary") or "").strip()
        if not desc:
            return Profile(None, "none")
        if signals.is_fund(r.fundamentals):
            return Profile(excerpt(desc), "description")  # what a fund holds; the company prompt doesn't fit it
        key = f"profile:{sec.market}:{sec.symbol}:{hashlib.sha1(desc.encode()).hexdigest()[:12]}"
        if (hit := self.store.cache_get(key)) is not None:
            return Profile(hit["text"], hit["source"])
        try:
            text = plain_text(await self.llm.chat(
                [{"role": "system", "content": PROFILE_SYSTEM}, {"role": "user", "content": f"DESCRIPTION:\n{desc}"}]
            ))
        except Exception as e:
            log.warning("profile LLM failed: %s", e)
            return Profile(excerpt(desc), "description")  # not cached: try the model again next time
        lines = [ln.strip(" -•") for ln in text.splitlines() if ln.strip()][:3]
        text = "\n".join(lines)
        profile = Profile(excerpt(desc), "description") if not text or invents_numbers(text, desc) else Profile(text, "model")
        self.store.cache_set(key, {"text": profile.text, "source": profile.source}, PROFILE_TTL)
        return profile

    async def conviction_summary(self, c: Conviction) -> str:
        """The model's explanation of the verdict. Cached by what the model would read, so asking again about
        a stock whose rules, quarter and headlines haven't changed answers at once instead of in a minute."""
        data = explain_data(c)
        key = "why:" + hashlib.sha1(f"{self.settings.ollama_model}\n{CONVICTION_SYSTEM}\n{data}".encode()).hexdigest()[:16]
        if (hit := self.store.cache_get(key)) is not None:
            return hit
        try:
            text = first_sentences(plain_text(await self.llm.chat(
                [{"role": "system", "content": CONVICTION_SYSTEM},
                 {"role": "user", "content": f"DATA:\n{data}\n\nExplain the verdict."}],
                max_tokens=WHY_MAX_TOKENS,
            )))
        except Exception as e:
            log.warning("LLM failed: %s", e)
            return f"(The local model is unavailable: {e}. Is `ollama serve` running?)"
        if text:  # a failure or an empty reply is not kept: the next ask tries the model again
            self.store.cache_set(key, text, WHY_TTL)
        return text
