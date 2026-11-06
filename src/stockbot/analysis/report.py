"""Gather everything about one stock, render it for Telegram, and build the LLM's DATA block."""

import logging
from dataclasses import dataclass, field
from html import escape

from ..config import Settings
from ..data import filings_india, filings_us, market, news
from ..llm.client import LLM
from ..llm.prompts import IMPACT_SYSTEM, Impacts, ItemImpact
from ..models import Item, Security
from ..store import Store
from . import fmt, metrics, signals

log = logging.getLogger(__name__)
DISCLAIMER = "<i>Not investment advice. Data: Yahoo Finance, NSE/SEC filings, Google News.</i>"
IMPACT_ICON = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}


@dataclass
class Research:
    sec: Security
    fundamentals: dict
    metrics: dict
    signals: list[signals.Signal]
    updates: list[Item] = field(default_factory=list)


def filings(sec: Security, settings: Settings) -> list[Item]:
    if sec.market == "IN":
        return filings_india.nse_announcements(sec.symbol)
    if sec.cik:
        return filings_us.edgar_filings(sec.cik, settings.sec_user_agent)
    return []


def updates(sec: Security, settings: Settings) -> list[Item]:
    """Exchange/SEC filings plus news, newest first. A failing source is logged, not fatal."""
    items: list[Item] = []
    for fetch in (lambda: filings(sec, settings), lambda: news.company_news(sec)):
        try:
            items.extend(fetch())
        except Exception as e:
            log.warning("updates for %s: %s", sec.symbol, e)
    return news.sort_newest(items)


def gather(sec: Security, store: Store, settings: Settings, with_updates: bool = True) -> Research:
    f = market.fetch_fundamentals(sec, store)
    m = metrics.compute(f)
    return Research(sec, f, m, signals.evaluate(m, f), updates(sec, settings) if with_updates else [])


def statement_currency(f: dict) -> str | None:
    """Yahoo can quote a price in one currency and report statements in another (INFY.NS: INR / USD)."""
    return f.get("financial_currency") or f.get("currency")


# --- LLM context ---

def data_block(r: Research, max_items: int = 12) -> str:
    f, m, cur = r.fundamentals, r.metrics, r.fundamentals.get("currency")
    fcur = statement_currency(f)
    lines = [
        f"STOCK: {r.sec.label}",
        f"Sector: {f.get('sector') or 'n/a'} / {f.get('industry') or 'n/a'}",
        f"Price: {fmt.price(f.get('price'), cur)} (day {fmt.pct(m.get('day_change'))}), "
        f"52w range {fmt.price(f.get('low_52w'), cur)}-{fmt.price(f.get('high_52w'), cur)}",
        f"Market cap: {fmt.money(f.get('market_cap'), cur)}; P/E {f.get('pe') and round(f['pe'], 1)}; "
        f"P/B {f.get('pb') and round(f['pb'], 2)}; ROE {fmt.pct(m.get('roe'), False)}; "
        f"net margin {fmt.pct(m.get('net_margin'), False)}; debt/equity "
        f"{m.get('debt_to_equity') and round(m['debt_to_equity'], 2)}; current ratio {f.get('current_ratio')}",
        "Grades (use these words when describing the ratios): "
        + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in signals.bands(m, f).items()),
        "Quarterly results (newest first):",
    ]
    if m.get("quarter_stale"):
        lines.append(
            f"  WARNING: the latest quarter available is {fmt.quarter_label(m['quarter'])}, which is out of date. "
            "Say that the newest quarterly numbers are not available; rely on the news and filings for recent results."
        )
    for q in (f.get("quarterly") or [])[:5]:
        lines.append(
            f"  {fmt.quarter_label(q['period'])}: revenue {fmt.money(q.get('revenue'), fcur)}, "
            f"operating profit {fmt.money(q.get('operating_income'), fcur)}, "
            f"net profit {fmt.money(q.get('net_income'), fcur)}, EPS {q.get('eps')}"
        )
    lines.append(
        f"Latest quarter change: revenue QoQ {fmt.pct(m.get('rev_qoq'))}, YoY {fmt.pct(m.get('rev_yoy'))}; "
        f"net profit QoQ {fmt.pct(m.get('ni_qoq'))}, YoY {fmt.pct(m.get('ni_yoy'))}"
    )
    lines.append("Annual (fiscal year end, newest first):")
    annual = (f.get("annual") or [])[:4]
    for i, a in enumerate(annual):
        prev = annual[i + 1] if i + 1 < len(annual) else {}
        lines.append(
            f"  FY ending {a['period']}: revenue {fmt.money(a.get('revenue'), fcur)} "
            f"({fmt.pct(metrics.pct_change(a.get('revenue'), prev.get('revenue')))} vs prior year), "
            f"net profit {fmt.money(a.get('net_income'), fcur)} "
            f"({fmt.pct(metrics.pct_change(a.get('net_income'), prev.get('net_income')))} vs prior year), "
            f"free cash flow {fmt.money(a.get('fcf'), fcur)}"
        )
    lines.append("Rule-based strengths: " + ("; ".join(s.text for s in r.signals if s.kind == "strength") or "none"))
    lines.append("Rule-based weaknesses: " + ("; ".join(s.text for s in r.signals if s.kind == "weakness") or "none"))
    if r.updates:
        lines.append("Recent filings and news (newest first):")
        for it in r.updates[:max_items]:
            when = it.published.strftime("%d %b %Y") if it.published else "date n/a"
            extra = f" — {it.summary[:200]}" if it.summary and it.kind == "filing" else ""
            lines.append(f"  [{when}] ({it.source}) {it.title}{extra}")
    return "\n".join(lines)


async def classify(llm: LLM, sec: Security, items: list[Item]) -> dict[str, ItemImpact]:
    """One LLM call tags every item with impact and importance."""
    if not items:
        return {}
    listing = "\n".join(f"{i}. ({it.source}) {it.title} {it.summary[:200]}".strip() for i, it in enumerate(items))
    try:
        result = await llm.structured(
            [
                {"role": "system", "content": IMPACT_SYSTEM.format(stock=sec.label)},
                {"role": "user", "content": listing},
            ],
            Impacts,
        )
    except Exception as e:
        log.warning("impact classification failed for %s: %s", sec.symbol, e)
        return {}
    return {items[x.index].key: x for x in result.items if 0 <= x.index < len(items)}


# --- Telegram rendering (HTML parse mode) ---

def header(r: Research) -> str:
    f, m, cur = r.fundamentals, r.metrics, r.fundamentals.get("currency")
    return (
        f"<b>{escape(f.get('name') or r.sec.name)}</b> · {escape(r.sec.symbol)} "
        f"({'NSE' if r.sec.market == 'IN' else 'US'})\n"
        f"{fmt.price(f.get('price'), cur)} ({fmt.pct(m.get('day_change'))}) · "
        f"Mcap {fmt.money(f.get('market_cap'), cur)} · P/E {round(f['pe'], 1) if f.get('pe') else 'n/a'}\n"
        f"<i>{escape(f.get('sector') or '')}{' / ' + escape(f['industry']) if f.get('industry') else ''}</i>"
    )


def quarter_lines(r: Research) -> str:
    m, cur = r.metrics, statement_currency(r.fundamentals)
    if not m.get("quarter"):
        return "<b>Latest quarter</b>\nNo quarterly data available."
    stale = (
        f"\n<i>⚠️ Yahoo has no quarter after {fmt.quarter_label(m['quarter'])}; check /news for newer results.</i>"
        if m.get("quarter_stale")
        else ""
    )
    return (
        f"<b>Latest quarter ({fmt.quarter_label(m['quarter'])})</b>\n"
        f"Revenue {fmt.money(m.get('q_revenue'), cur)} (QoQ {fmt.pct(m.get('rev_qoq'))}, "
        f"YoY {fmt.pct(m.get('rev_yoy'))})\n"
        f"Net profit {fmt.money(m.get('q_net_income'), cur)} (QoQ {fmt.pct(m.get('ni_qoq'))}, "
        f"YoY {fmt.pct(m.get('ni_yoy'))})" + stale
    )


def render_analysis(r: Research, summary: str | None) -> str:
    strengths = [s for s in r.signals if s.kind == "strength"]
    weaknesses = [s for s in r.signals if s.kind == "weakness"]
    parts = [header(r), quarter_lines(r)]
    parts.append("<b>✅ Strengths</b>\n" + ("\n".join(f"• {escape(s.text)}" for s in strengths) or "• None flagged by the rules"))
    parts.append("<b>⚠️ Weaknesses</b>\n" + ("\n".join(f"• {escape(s.text)}" for s in weaknesses) or "• None flagged by the rules"))
    parts.append("<b>🧠 Summary</b>\n" + (escape(summary) if summary else "<i>writing summary with the local model…</i>"))
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def render_results(r: Research, summary: str | None) -> str:
    cur = statement_currency(r.fundamentals)
    rows = [
        f"{fmt.quarter_label(q['period']):>8} | {fmt.money(q.get('revenue'), cur):>14} | {fmt.money(q.get('net_income'), cur):>13}"
        for q in (r.fundamentals.get("quarterly") or [])[:5]
    ]
    table = f"{'Quarter':>8} | {'Revenue':>14} | {'Net profit':>13}\n" + "\n".join(rows)
    filings_ = [it for it in r.updates if "results" in it.tags][:4]
    parts = [header(r), quarter_lines(r), f"<pre>{escape(table)}</pre>"]
    if filings_:
        parts.append("<b>Result filings</b>\n" + "\n".join(item_line(it) for it in filings_))
    parts.append("<b>🧠 Summary</b>\n" + (escape(summary) if summary else "<i>writing summary with the local model…</i>"))
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def item_line(it: Item, impact: ItemImpact | None = None) -> str:
    when = it.published.strftime("%d %b") if it.published else ""
    icon = IMPACT_ICON[impact.impact] + " " if impact else "• "
    title = escape(" ".join(it.title.split()))
    link = f'<a href="{escape(it.url, quote=True)}">{title}</a>' if it.url else title
    reason = f"\n   <i>{escape(impact.reason)}</i>" if impact and impact.importance != "low" else ""
    return f"{icon}{link} — {escape(it.source)} {when}{reason}"


def render_updates(sec: Security, items: list[Item], impacts: dict[str, ItemImpact]) -> str:
    if not items:
        return f"No recent filings or news found for {escape(sec.label)}."
    body = "\n".join(item_line(it, impacts.get(it.key)) for it in items)
    return f"<b>📰 Latest on {escape(sec.label)}</b>\n\n{body}\n\n{DISCLAIMER}"


def clip(html: str, limit: int = 4000) -> str:
    """Telegram rejects messages over 4096 chars; cut on a line boundary so tags stay balanced."""
    if len(html) <= limit:
        return html
    cut = html.rfind("\n", 0, limit)
    return html[: cut if cut > 0 else limit] + "\n…"
