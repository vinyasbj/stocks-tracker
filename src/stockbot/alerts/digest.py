"""Portfolio P&L and wishlist prices, for /portfolio and the daily digest."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

from ..analysis import fmt, report
from ..data import market, news
from ..models import Holding

log = logging.getLogger(__name__)


def currency(h: Holding) -> str:
    return "INR" if h.sec.market == "IN" else "USD"


@dataclass
class Position:
    holding: Holding
    price: float
    prev: float | None
    high_52w: float | None

    @property
    def day_pct(self) -> float | None:
        return (self.price / self.prev - 1) * 100 if self.prev else None

    @property
    def value(self) -> float | None:
        return self.price * self.holding.qty if self.holding.qty else None

    @property
    def day_value(self) -> float | None:
        if not self.holding.qty or not self.prev:
            return None
        return (self.price - self.prev) * self.holding.qty

    @property
    def invested(self) -> float | None:
        h = self.holding
        return h.qty * h.avg_price if h.qty and h.avg_price else None

    @property
    def pnl(self) -> float | None:
        return self.value - self.invested if self.value is not None and self.invested is not None else None


def totals(positions: list[Position]) -> dict[str, dict[str, float]]:
    """Per currency (no FX conversion): value, day change, invested and P&L of positions with full data."""
    out: dict[str, dict[str, float]] = {}
    for p in positions:
        if p.value is None:
            continue
        t = out.setdefault(currency(p.holding), {"value": 0.0, "day": 0.0, "invested": 0.0, "pnl_value": 0.0})
        t["value"] += p.value
        t["day"] += p.day_value or 0.0
        if p.invested is not None:
            t["invested"] += p.invested
            t["pnl_value"] += p.value
    return out


def portfolio_line(p: Position) -> str:
    h, cur = p.holding, currency(p.holding)
    line = f"{h.icon} <b>{escape(h.sec.symbol)}</b> {fmt.price(p.price, cur)} ({fmt.pct(p.day_pct)})"
    if h.qty:
        qty = f"{h.qty:g}"
        line += f"\n   {qty} sh = {fmt.amount(p.value, cur)} · day {fmt.amount(p.day_value, cur, signed=True)}"
    if p.pnl is not None:
        line += f" · P&L {fmt.amount(p.pnl, cur, signed=True)} ({fmt.pct(p.pnl / p.invested * 100)})"
    return line


def wishlist_line(p: Position) -> str:
    h, cur = p.holding, currency(p.holding)
    line = f"{h.icon} <b>{escape(h.sec.symbol)}</b> {fmt.price(p.price, cur)} ({fmt.pct(p.day_pct)})"
    if p.high_52w:
        line += f" · {fmt.pct((p.price / p.high_52w - 1) * 100)} from 52w high"
    return line


def totals_lines(positions: list[Position]) -> list[str]:
    lines = []
    for cur, t in totals(positions).items():
        prev = t["value"] - t["day"]
        line = (
            f"<b>Total {cur}</b> {fmt.amount(t['value'], cur)} · day {fmt.amount(t['day'], cur, signed=True)}"
            f" ({fmt.pct(t['day'] / prev * 100 if prev else None)})"
        )
        if t["invested"]:
            pnl = t["pnl_value"] - t["invested"]
            line += f" · P&L {fmt.amount(pnl, cur, signed=True)} ({fmt.pct(pnl / t['invested'] * 100)})"
        lines.append(line)
    return lines


def _position(h: Holding) -> Position | None:
    try:
        q = market.fetch_quote(h.sec)
    except Exception as e:
        log.warning("quote %s: %s", h.sec.symbol, e)
        return None
    return Position(h, q["price"], q.get("prev"), q.get("high_52w"))


def _headlines(h: Holding) -> str:
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = [i for i in news.company_news(h.sec, limit=10) if i.published and i.published >= since][:2]
        return "".join("\n   " + report.item_line(i) for i in recent)
    except Exception as e:
        log.warning("digest news %s: %s", h.sec.symbol, e)
        return ""


async def build(holdings: list[Holding], with_news: bool) -> str | None:
    if not holdings:
        return None
    positions = await asyncio.gather(*(asyncio.to_thread(_position, h) for h in holdings))
    heads = (
        await asyncio.gather(*(asyncio.to_thread(_headlines, h) for h in holdings))
        if with_news
        else [""] * len(holdings)
    )
    port, wish = [], []
    for h, p, head in zip(holdings, positions, heads):
        if p is None:
            line = f"{h.icon} <b>{escape(h.sec.symbol)}</b> price unavailable"
        else:
            line = portfolio_line(p) if h.list_type == "portfolio" else wishlist_line(p)
        (port if h.list_type == "portfolio" else wish).append(line + head)
    sections = []
    if port:
        valid = [p for p in positions if p and p.holding.list_type == "portfolio"]
        sections.append("<b>💼 Portfolio</b>\n" + "\n".join(port + [""] + totals_lines(valid)).rstrip())
    if wish:
        sections.append("<b>⭐ Wishlist</b>\n" + "\n".join(wish))
    title = "☀️ <b>Daily digest</b>" if with_news else "<b>Your stocks</b>"
    return report.clip("\n\n".join([title, *sections, report.DISCLAIMER]))
