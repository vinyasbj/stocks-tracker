import asyncio
import json
from pathlib import Path

import pytest

from stockbot.analysis import conviction as cv
from stockbot.analysis import metrics, report, signals
from stockbot.data import notion
from stockbot.engine import Engine
from stockbot.models import Holding, Security
from stockbot.store import Store

from .conftest import make_settings
from .test_blueprint import csv_pages

FIX = Path(__file__).parent / "fixtures"
GOOGL = Security("GOOGL", "US", "Alphabet Inc.")
TCS = Security("TCS", "IN", "Tata Consultancy Services Limited")
JUNK = Security("JUNK", "US", "Junk Corp")
BLUEPRINT_DB = "1a2b3c4d5e6f40718293a4b5c6d7e8f0"
DESC = "Alphabet operates through Google Services, Google Cloud, and Other Bets segments in the United States, Europe and Asia."


def q(period, revenue, net_income, operating_income=None):
    return {"period": period, "revenue": revenue, "net_income": net_income, "operating_income": operating_income, "eps": None}


def fundamentals(**over):
    f = {
        "name": "Alphabet Inc.", "sector": "Communication Services", "industry": "Internet Content & Information",
        "currency": "USD", "financial_currency": "USD", "country": "United States", "business_summary": DESC,
        "price": 170.0, "prev_close": 168.0, "high_52w": 190.0, "low_52w": 140.0, "market_cap": 2.1e12,
        "pe": 24.0, "roe": 0.31, "net_margin": 0.29, "operating_margin": 0.32, "current_ratio": 2.1,
        "debt_to_equity_pct": 10.0,
        "quarterly": [q("2026-09-30", 100e9, 28e9, 32e9), q("2026-06-30", 96e9, 26e9, 30e9), q("2025-09-30", 88e9, 24e9, 28e9)],
        "annual": [
            {"period": "2025-12-31", "revenue": 380e9, "net_income": 110e9, "fcf": 70e9},
            {"period": "2024-12-31", "revenue": 350e9, "net_income": 100e9, "fcf": 69e9},
            {"period": "2023-12-31", "revenue": 307e9, "net_income": 74e9, "fcf": 69e9},
            {"period": "2022-12-31", "revenue": 283e9, "net_income": 60e9, "fcf": 60e9},
        ],
    }
    f.update(over)
    return f


LOSS_MAKER = fundamentals(
    name="Junk Corp", net_margin=-0.12, pe=None, roe=-0.2,
    quarterly=[q("2026-09-30", 10e9, -1e9, -1e9), q("2025-09-30", 9e9, -2e9, -2e9)],
    annual=[{"period": "2025-12-31", "revenue": 40e9, "net_income": -5e9, "fcf": -1e9},
            {"period": "2024-12-31", "revenue": 38e9, "net_income": -6e9, "fcf": -2e9}],
)


def research(sec, store=None, settings=None, with_updates=True, data=None):
    f = data or {"JUNK": LOSS_MAKER}.get(sec.symbol) or fundamentals()
    m = metrics.compute(f)
    return report.Research(sec, f, m, signals.evaluate(m, f), [])


def thesis_blocks(ticked=True):
    blocks = json.loads((FIX / "notion_thesis_blocks.json").read_text())["results"]
    for b in blocks:
        if b["type"] == "to_do":
            b["to_do"]["checked"] = ticked
    return blocks


class FakeLLM:
    def __init__(self, reply="Google Services sells ads.\nGoogle Cloud sells cloud services.", fail=False):
        self.reply, self.fail, self.calls = reply, fail, 0

    async def chat(self, messages, temperature=0.3):
        self.calls += 1
        if self.fail:
            raise ConnectionError("ollama down")
        return self.reply


@pytest.fixture
def eng(monkeypatch):
    e = Engine(make_settings(notion_blueprint_db=BLUEPRINT_DB), Store(":memory:"))
    e.notion_pages = csv_pages()  # what the Notion blueprint database holds
    e.notion_down = False

    def query_pages(token, database):
        if e.notion_down:
            raise ConnectionError("Notion is down")
        assert database == BLUEPRINT_DB
        return e.notion_pages

    e.blocks = {}  # page id -> blocks, or an exception to raise

    def page_blocks(token, page):
        got = e.blocks.get(page, [])
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(notion, "query_pages", query_pages)
    monkeypatch.setattr(notion, "page_blocks", page_blocks)
    monkeypatch.setattr(report, "gather", research)
    e.llm = FakeLLM()
    return e


def run(coro):
    return asyncio.run(coro)


def edit_threshold(pages, rule, value):
    out = json.loads(json.dumps(pages))
    for p in out:
        if p["properties"]["Rule"]["title"][0]["plain_text"] == rule:
            p["properties"]["Threshold"] = {"type": "rich_text", "rich_text": [{"plain_text": str(value)}]}
    return out


# --- 5.2 blueprint status and lock ---

def test_lock_versions(eng):
    first = run(eng.lock_blueprint())
    assert first.version == 1
    again = run(eng.lock_blueprint())
    assert again.version is None and again.unchanged
    eng.notion_pages = edit_threshold(csv_pages(), "Return on equity at least 15%", 12)
    st = run(eng.blueprint_status())
    assert st.locked.version == 1 and st.diff.summary() == "1 changed"
    second = run(eng.lock_blueprint())
    assert second.version == 2 and second.status.diff.summary() == "1 changed"
    assert eng.store.blueprint_versions() == [1, 2]


def test_invalid_rows_refuse_the_lock(eng):
    eng.notion_pages = edit_threshold(csv_pages(), "Return on equity at least 15%", "")
    res = run(eng.lock_blueprint())
    assert res.version is None and eng.store.locked_blueprint() is None
    assert "threshold is missing" in "\n".join(cv.render_lock(res))


def test_notion_down_keeps_the_locked_version(eng):
    run(eng.lock_blueprint())
    eng.notion_down = True
    st = run(eng.blueprint_status())
    assert st.locked.version == 1 and "Notion is down" in st.error
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150)])
    c = run(eng.conviction(GOOGL))
    assert c.verdict.code in ("ADD", "HOLD") and c.unlocked is None


def test_no_blueprint_database_explains_the_setup(eng):
    eng.settings = make_settings(notion_blueprint_db="")
    st = run(eng.blueprint_status())
    assert st.error and st.locked is None
    text = "\n".join(cv.render_blueprint(st))
    assert "NOTION_BLUEPRINT_DB" in text and "/blueprint lock" in text


def test_nothing_locked_means_no_verdict(eng):
    assert run(eng.conviction(GOOGL)) is None
    assert run(eng.conviction_report()).locked is None


# --- 5.3 one stock and the report ---

def test_holding_with_a_ticked_thesis(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150, ("pg1",), None)])
    eng.blocks["pg1"] = thesis_blocks(ticked=True)
    c = run(eng.conviction(GOOGL))
    assert c.held and c.thesis is not None and c.conviction == "HIGH"
    judged = {ch.rule.text: ch.status for ch in c.verdict.checks if ch.rule.is_judgment}
    assert judged["I understand how the company makes money"] == "pass"
    assert judged["No promoter pledging or governance red flags"] == "unknown"  # no tick box on the page
    assert c.verdict.code == "HOLD"


def test_column_conviction_wins_over_the_page(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150, ("pg1",), "LOW")])
    eng.blocks["pg1"] = thesis_blocks()
    assert run(eng.conviction(GOOGL)).conviction == "LOW"


def test_unreadable_page_still_gives_a_verdict(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150, ("pg1",))])
    eng.blocks["pg1"] = PermissionError("Notion can't find page pg1. Open it in Notion, ⋯ → Connections, and add your integration.")
    c = run(eng.conviction(GOOGL))
    assert c.thesis is None and "could not be read" in c.thesis_note
    assert {ch.note for ch in c.verdict.checks if ch.rule.is_judgment} == {"thesis could not be read"}
    assert c.verdict.code == "HOLD"


def test_second_lot_holds_the_thesis(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 5, 150, ("lot1",)), Holding(GOOGL, "portfolio", 5, 160, ("lot2",))])
    eng.blocks["lot2"] = thesis_blocks()
    assert run(eng.conviction(GOOGL)).thesis.page_id == "lot2"


def test_stock_not_in_portfolio_gets_buy_watch_avoid(eng):
    run(eng.lock_blueprint())
    c = run(eng.conviction(TCS))
    assert not c.held and c.verdict.code == "WATCH"  # judgment rules can't be confirmed without a thesis
    assert run(eng.conviction(JUNK)).verdict.code == "AVOID"  # loss-making two years running


def test_report_survives_one_failing_stock(eng, monkeypatch):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 1, 1), Holding(JUNK, "portfolio", 1, 1),
                                Holding(TCS, "portfolio", 1, 1), Holding(Security("AAPL", "US", "Apple"), "wishlist")])

    def gather(sec, *a, **k):
        if sec.symbol == "TCS":
            raise LookupError("Yahoo Finance has no data for TCS.NS")
        return research(sec)

    monkeypatch.setattr(report, "gather", gather)
    rep = run(eng.conviction_report())
    assert [e.holding.sec.symbol for e in rep.entries] == ["GOOGL", "JUNK", "TCS"]  # the wishlist is ignored
    by = {e.holding.sec.symbol: e for e in rep.entries}
    assert by["JUNK"].conviction.verdict.code == "SELL"
    assert by["TCS"].conviction is None and "no data" in by["TCS"].error
    text = "\n".join(cv.render_report(rep))
    assert "3 holdings: 1 sell · 0 review · 1 hold · 0 add · 1 unavailable" in text
    assert "<b>JUNK</b>" in text and "Consecutive loss years 2 years (need ≤ 1 year)" in text
    assert "No thesis yet: GOOGL, JUNK" in text


# --- 5.6 business profile ---

def test_profile_from_the_model_is_cached(eng):
    r = research(GOOGL)
    p1 = run(eng.business_profile(GOOGL, r))
    p2 = run(eng.business_profile(GOOGL, r))
    assert p1 == p2 and p1.source == "model" and p1.text.startswith("Google Services sells ads.")
    assert eng.llm.calls == 1


def test_profile_with_an_invented_number_falls_back_to_the_description(eng):
    eng.llm = FakeLLM("Google Services makes 77% of revenue.")
    p = run(eng.business_profile(GOOGL, research(GOOGL)))
    assert p.source == "description" and p.text.startswith("Alphabet operates through Google Services")


def test_profile_when_the_model_is_down(eng):
    eng.llm = FakeLLM(fail=True)
    p = run(eng.business_profile(GOOGL, research(GOOGL)))
    assert p.source == "description"
    eng.llm = FakeLLM()
    assert run(eng.business_profile(GOOGL, research(GOOGL))).source == "model"  # the fallback was not cached


def test_profile_without_a_description(eng):
    r = research(GOOGL, data=fundamentals(business_summary=None))
    p = run(eng.business_profile(GOOGL, r))
    assert p == cv.Profile(None, "none") and eng.llm.calls == 0
    assert "no company description" in cv.profile_block(r, p)


# --- 5.5 rendering ---

def test_check_message(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150, ("pg1",), "HIGH")])
    eng.blocks["pg1"] = thesis_blocks()
    c = run(eng.conviction(GOOGL))
    html = cv.render_check(c, run(eng.business_profile(GOOGL, c.research)), "It fits.")
    for part in ("🔵 <b>VERDICT: HOLD</b>", "Your conviction: <b>HIGH</b>", "v1", "BUSINESS & PROFIT",
                 "Google Cloud", cv.NO_SPLIT, "Latest quarter (Sep 2026): net profit $28.00B · YoY +16.7% · QoQ +7.7%",
                 "Full year to Dec 2025: net profit $110.00B · profit CAGR 22.4% over 3y",
                 "<b>Quality</b>", "✅ ROE ≥ 15.0%: 31.0% · <i>must</i>",
                 "<u>One-line thesis</u>: Search is still a cash machine", "<b>── 🧠 SUMMARY ──</b>\nIt fits.",
                 "Blueprint v1"):
        assert part in html, part
    assert len(html) <= cv.TELEGRAM_LIMIT


def test_check_message_for_a_loss_maker_not_owned(eng):
    run(eng.lock_blueprint())
    c = run(eng.conviction(JUNK))
    html = cv.render_check(c, cv.Profile(None, "none"), None)
    assert "🔴 <b>VERDICT: AVOID</b>" in html
    assert "<b>Loss-making</b>: net loss $1.00B in the latest quarter" in html
    assert "Full year to Dec 2025: net loss $5.00B" in html
    assert "not in your portfolio" in html


def test_unlocked_edits_are_noted(eng):
    run(eng.lock_blueprint())
    eng.notion_pages = edit_threshold(csv_pages(), "P/E at most 40", 30)
    c = run(eng.conviction(GOOGL))
    assert "Notion differs: 1 changed. Still using v1" in cv.render_check(c, None, None)


def test_long_report_is_split_not_cut():
    from stockbot.analysis.blueprint import Blueprint, Locked
    from stockbot.analysis.verdict import Verdict

    locked = Locked(1, 0, Blueprint([]))
    entries = []
    for i in range(40):
        h = Holding(Security(f"STOCK{i:02d}", "IN", f"Stock {i}"), "portfolio", 1, 1)
        v = Verdict("HOLD", "", [], True, "confirm: " + "a long judgment rule text " * 6)
        c = cv.Conviction(h.sec, None, h, locked, v)
        entries.append(cv.ReportEntry(h, c))
    msgs = cv.render_report(cv.ReportResult(locked, entries))
    assert len(msgs) >= 2 and all(len(m) <= cv.TELEGRAM_LIMIT for m in msgs)
    joined = "\n".join(msgs)
    assert all(f"<b>STOCK{i:02d}</b>" in joined for i in range(40))


def test_blueprint_view_has_the_tick_box_block(eng):
    run(eng.lock_blueprint())
    text = "\n".join(cv.render_blueprint(run(eng.blueprint_status())))
    assert "Your conviction blueprint</b> · v1" in text and "Scored rules pass bar: <b>75%</b>" in text
    assert text.count("- [ ] ") == 4 and "## Blueprint checks" in text
    assert "⭐ Profit-making: not loss-making in each of the last 2 years" in text


def test_split_messages_keeps_every_line():
    lines = [f"line {i} " + "x" * 90 for i in range(100)]
    msgs = cv.split_messages(lines, limit=1000)
    assert "\n".join(msgs).split("\n") == lines and all(len(m) <= 1000 for m in msgs)
