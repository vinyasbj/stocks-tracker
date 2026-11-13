import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

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
        self.last_messages = self.last_max_tokens = None

    async def chat(self, messages, temperature=0.3, max_tokens=None):
        self.calls += 1
        self.last_messages, self.last_max_tokens = messages, max_tokens
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
    assert not c.held and c.verdict.code == "BUY"  # judgment rules don't block a stock you don't own...
    assert c.verdict.reason == "Numbers pass every rule, valuation included; before buying, confirm 4 judgment rules"
    html = cv.render_check(c, None, None)
    assert "➖ 4 judgment rules: yours to confirm before buying" in html  # ...they are left for you to answer
    assert "· 11 of 11 pass" in html  # and they don't count as failures
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
    assert "3 holdings · 🔴 1 sell · 🟠 0 review · 🔵 1 hold · 🟢 0 add · ❔ 1 unavailable" in text
    assert "<pre>Stock  Rule broken    Value\nJUNK   Loss years   2 years\n       +ROE          -20.0%</pre>" in text
    assert "<i>Limits: Loss years ≤ 1 year · ROE ≥ 15.0%</i>" in text
    assert "🔵 <b>HOLD</b>\n<pre>Confirm 4 judgment rules\nGOOGL</pre>" in text
    assert "<b>TCS</b> Yahoo Finance has no data for TCS.NS" in text
    assert "No thesis yet: GOOGL, JUNK" in text
    assert "(need " not in text  # each rule's limit is said once, under the table


def report_of(eng, monkeypatch, holdings, data):
    """The report for `holdings`, with each symbol's fundamentals taken from `data`."""
    run(eng.lock_blueprint())
    eng.store.replace_holdings(holdings)

    async def no_sync(**_):
        return None

    eng.sync = no_sync
    monkeypatch.setattr(report, "gather", lambda sec, *a, **k: research(sec, data=data.get(sec.symbol)))
    return "\n".join(cv.render_report(run(eng.conviction_report())))


def test_report_groups_stocks_by_the_rule_they_break(eng, monkeypatch):
    heavy = fundamentals(debt_to_equity_pct=250.0)
    weak = fundamentals(roe=0.10)
    syms = ["AAA", "BBB", "CCC"]
    text = report_of(eng, monkeypatch, [Holding(Security(s, "US", s), "portfolio", 1, 1) for s in syms],
                     {"AAA": heavy, "BBB": heavy, "CCC": weak})
    rows = text.split("<pre>")[1].split("</pre>")[0].split("\n")
    assert [r.split()[:2] for r in rows[1:]] == [["AAA", "Debt/Equity"], ["BBB", "Debt/Equity"], ["CCC", "ROE"]]  # bigger group first
    assert len({len(r) for r in rows}) == 1  # values right-aligned in one column
    assert "Limits: Debt/Equity ≤ 1.00 · ROE ≥ 15.0%" in text
    assert "🔴 3 sell" in text


def test_report_does_not_judge_etfs(eng, monkeypatch):
    etf = {"name": "Invesco NASDAQ 100 ETF", "quote_type": "ETF", "currency": "USD", "price": 250.0}
    text = report_of(eng, monkeypatch, [Holding(Security("QQQM", "US", "Invesco"), "portfolio", 1, 1),
                                        Holding(GOOGL, "portfolio", 1, 1)],
                     {"QQQM": etf})
    assert "⚪ <b>Not judged</b> · ETFs and funds\n<pre>QQQM</pre>" in text
    assert "⚪ 1 funds" in text and "🔵 1 hold" in text
    assert "QQQM" not in text.split("Not judged")[0]  # no verdict, no "no thesis" for the fund


def test_report_syncs_notion_first(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 1, 1), Holding(JUNK, "portfolio", 1, 1)])

    async def sync(**_):  # JUNK was removed from the Notion portfolio since the last sync
        eng.store.replace_holdings([Holding(GOOGL, "portfolio", 1, 1)])

    eng.sync = sync
    rep = run(eng.conviction_report())
    assert [e.holding.sec.symbol for e in rep.entries] == ["GOOGL"] and rep.sync_error is None


def test_report_says_when_notion_could_not_be_reread(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 1, 1)])

    async def sync(**_):
        raise ConnectionError("Notion is down")

    eng.sync = sync
    text = "\n".join(cv.render_report(run(eng.conviction_report())))
    assert "Couldn't re-read Notion, so this uses the last synced list (Notion is down)" in text
    assert "Confirm 4 judgment rules\nGOOGL" in text


def test_unreadable_thesis_is_not_called_missing(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 1, 1, ("gone",))])
    eng.blocks["gone"] = LookupError("Notion can't find page gone")
    text = "\n".join(cv.render_report(run(eng.conviction_report())))
    assert "Thesis page unreadable (share it with your Notion integration): GOOGL" in text
    assert "No thesis yet" not in text


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
    for part in ("🔵 <b>VERDICT: HOLD</b> · blueprint v1", "Your conviction: <b>HIGH</b>", "<b>🏢 Business & profit</b>",
                 "Google Cloud", "Latest quarter (Sep 2026): net profit $28.00B · YoY +16.7% · QoQ +7.7%",
                 "Full year to Dec 2025: net profit $110.00B · profit CAGR 22.4% over 3y",
                 "<b>📏 Rules</b> · 14 of 15 pass", "✅ Business: 3 judgment rules ticked",
                 "❓ Business: No promoter pledging or governance red flags · no tick box for it in Notion · <i>must</i>",
                 "· ROE 31.0% ·", "✅ Valuation: P/E 24.0",
                 "<u>One-line thesis</u>: Search is still a cash machine", "<u>Thesis breakers</u>",
                 "🧠 It fits.", "Blueprint v1"):
        assert part in html, part
    assert "not available from Yahoo" not in html
    assert len(html) <= cv.TELEGRAM_LIMIT


def test_check_message_for_a_holding_without_a_thesis(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150)])
    c = run(eng.conviction(GOOGL))
    html = cv.render_check(c, None, None)
    assert "Fits so far; confirm: 4 judgment rules" in html
    assert "Your conviction: not set" in html
    assert "❓ 4 judgment rules unconfirmed: no thesis in Notion · <i>must</i>" in html
    assert html.count("no thesis in Notion") == 1  # one rules line, not once per judgment rule
    assert "📝 <i>No thesis in Notion yet. Write it" in html
    assert "🧠 <i>writing summary" in html


def test_check_message_folds_rules_without_data_per_pillar(eng):
    run(eng.lock_blueprint())
    c = run(eng.conviction(JUNK))
    lines = cv.render_check(c, None, None).split("\n")
    unknown = [x for x in lines if x.startswith("❓ ") and "judgment" not in x]
    assert len(unknown) == len({x.split(":")[0] for x in unknown})  # at most one line per pillar


def test_summary_is_capped_at_three_sentences():
    from stockbot.engine import first_sentences

    text = "ROE is 24.3% in the U.S. market. Profit fell. Margins held. Watch Q4."
    assert first_sentences(text) == "ROE is 24.3% in the U.S. market. Profit fell. Margins held."


def test_a_long_check_shrinks_the_thesis_then_cuts_the_middle_never_the_summary(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150, ("pg1",), "HIGH")])
    eng.blocks["pg1"] = thesis_blocks()
    c = run(eng.conviction(GOOGL))
    profile = run(eng.business_profile(GOOGL, c.research))
    disclaimer = cv.DISCLAIMER.format(version=1)

    html = cv.render_check(c, profile, "s" * 2500)
    assert len(html) <= cv.TELEGRAM_LIMIT and html.endswith(disclaimer) and "s" * 2500 in html
    assert "<u>One-line thesis</u>" in html and "The rest of your thesis is in Notion" in html
    assert "<u>Thesis breakers</u>" not in html

    html = cv.render_check(c, profile, "s" * 3500)
    assert len(html) <= cv.TELEGRAM_LIMIT and html.endswith(disclaimer) and "s" * 3500 in html
    assert "VERDICT: HOLD" in html and "\n…\n" in html


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

    locked = Locked(1, 0, Blueprint([]))
    entries = []
    for i in range(80):  # one line each: stocks whose data failed are listed one per line
        h = Holding(Security(f"STOCK{i:02d}", "IN", f"Stock {i}"), "portfolio", 1, 1)
        entries.append(cv.ReportEntry(h, error="Yahoo Finance timed out " * 4))
    msgs = cv.render_report(cv.ReportResult(locked, entries))
    assert len(msgs) >= 2 and all(len(m) <= cv.TELEGRAM_LIMIT for m in msgs)
    joined = "\n".join(msgs)
    assert all(f"<b>STOCK{i:02d}</b>" in joined for i in range(80))


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


def test_a_holding_is_found_even_when_the_lookup_spells_the_name_differently(eng):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(Security("GOOGL", "US", "ALPHABET INC", 1652044), "portfolio", 10, 150)])
    c = run(eng.conviction(Security("GOOGL", "US", "Alphabet Inc.")))
    assert c.held and c.verdict.code in ("ADD", "HOLD", "REVIEW", "SELL")


@pytest.mark.parametrize("pct, now, base, said", [
    (16.7, 28e9, 24e9, "+16.7%"),
    (-96.6, -48.5e6, -24.7e6, "loss widened 97%"),
    (50.0, -1e9, -2e9, "loss narrowed 50%"),
    (-150.0, -1e9, 2e9, "swung to a loss"),
    (200.0, 1e9, -1e9, "back to profit"),
])
def test_profit_changes_are_said_in_words_when_a_loss_is_involved(pct, now, base, said):
    assert cv.profit_change(pct, now, base) == said


def test_loss_maker_profit_line_says_the_loss_narrowed():
    lines = cv.profit_lines(research(JUNK))
    assert lines[0].startswith("<b>Loss-making</b>: net loss $1.00B") and "YoY loss narrowed 50%" in lines[0]


def test_yahoo_zero_net_margin_without_revenue_is_no_data():
    f = fundamentals(net_margin=0.0, quarterly=[q("2026-06-30", 0, -48e6, -50e6), q("2025-06-30", 0, -25e6, -26e6)],
                     annual=[{"period": "2025-12-31", "revenue": 0, "net_income": -105e6, "fcf": -90e6}])
    assert metrics.compute(f)["net_margin"] is None
    assert metrics.compute(fundamentals(net_margin=0.0, quarterly=[q("2026-09-30", 10e9, 0, 1e9)]))["net_margin"] == 0  # a real 0%


def test_nothing_ticked_yet_is_one_line_and_some_ticked_lists_the_rest(eng):
    rules = ["I understand how the company makes money", "It has a durable moat (brand, network, cost or switching costs)",
             "Management is honest and allocates capital well", "No promoter pledging or governance red flags"]

    def boxes(ticked):
        return [{"type": "to_do", "to_do": {"rich_text": [{"plain_text": t}], "checked": t in ticked}} for t in rules]

    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 10, 150, ("pg1",), "HIGH")])
    eng.blocks["pg1"] = boxes(ticked=())
    html = cv.render_check(run(eng.conviction(GOOGL)), None, None)
    assert "❓ 4 judgment rules unconfirmed: not ticked in Notion · <i>must</i>" in html

    eng.blocks["pg1"] = boxes(ticked=rules[:1])
    html = cv.render_check(run(eng.conviction(GOOGL)), None, None)
    assert "judgment rules unconfirmed" not in html
    assert "❓ Business: Management is honest and allocates capital well · not ticked in Notion · <i>must</i>" in html
    assert "✅ Business: 1 judgment rule ticked" in html


QQQM = Security("QQQM", "US", "Invesco NASDAQ 100 ETF")
ETF = {
    "name": "Invesco NASDAQ 100 ETF", "quote_type": "ETF", "category": "Large Growth", "fund_family": "Invesco",
    "currency": "USD", "price": 270.22, "prev_close": 266.85, "high_52w": 314.12, "low_52w": 228.75, "pe": 27.0,
    "total_assets": 106.0e9, "expense_ratio_pct": 0.15, "ytd_return_pct": 12.83, "return_3y": 0.219, "return_5y": 0.112,
    "fund_yield": 0.0046, "business_summary": "The fund invests at least 90% of its assets in the Nasdaq-100 Index.",
}


def test_an_etf_ticker_is_found_by_exact_lookup(eng, monkeypatch):
    from stockbot.data import market

    URA = Security("URA", "US", "Global X Uranium ETF")
    funds = {"QQQM": QQQM, "URA": URA}
    asked = []

    def lookup(symbol, mkt, types=market.LISTED_TYPES):
        asked.append((symbol, mkt, types))
        return funds.get(symbol.upper())

    monkeypatch.setattr(market, "lookup_symbol", lookup)
    lists = {"googl": [GOOGL], "ura": [Security("ACCURACY", "IN", "Accuracy Shipping")], "infosys": [TCS]}
    eng._resolver = SimpleNamespace(resolve=lambda q: lists.get(q.lower(), []))

    assert run(eng.resolve("QQQM")) == [QQQM]  # in no exchange list: exact lookup
    assert run(eng.resolve("us:qqqm")) == [QQQM] and asked[-1][:2] == ("qqqm", "US")
    assert run(eng.resolve("URA")) == [URA]  # an exact ETF ticker beats fuzzy company-name matches
    assert asked[-1][2] == market.FUND_TYPES  # ...but only an ETF or fund, never another stock
    n = len(asked)
    assert run(eng.resolve("GOOGL")) == [GOOGL] and len(asked) == n  # an exact listed symbol: no lookup
    assert run(eng.resolve("infosys")) == [TCS]  # no fund called INFOSYS: the name matches stand
    assert run(eng.resolve("invesco nasdaq etf")) == [] and len(asked) == n + 1  # words are never a ticker


def test_why_shows_an_etf_without_judging_it(eng, monkeypatch):
    run(eng.lock_blueprint())
    eng.store.replace_holdings([Holding(QQQM, "portfolio", 5, 200, (), "HIGH")])
    monkeypatch.setattr(report, "gather", lambda sec, *a, **k: research(sec, data=ETF))
    c = run(eng.conviction(QQQM))
    assert c.fund and c.mismatch is None
    profile = run(eng.business_profile(QQQM, c.research))
    assert profile == cv.Profile(ETF["business_summary"], "description") and eng.llm.calls == 0
    html = cv.render_check(c, profile, None)
    for part in ("⚪ <b>NOT JUDGED</b> · ETF", "In your portfolio · your conviction: <b>HIGH</b>",
                 "Invesco · Large Growth", "Assets $106.00B · Expense ratio 0.15% · Yield 0.46%",
                 "YTD +12.8% · 3y avg +21.9%/yr · 5y avg +11.2%/yr", "52w range $228.75–$314.12 ·",
                 "<b>🏢 What it holds</b>\nThe fund invests at least 90%", "Funds aren't judged by your blueprint"):
        assert part in html, part
    assert "VERDICT" not in html and "📏 Rules" not in html


# --- /why: a short prompt, a capped reply, and a cache ---

def slow_grower():
    """Healthy business that grows about 5% a year: fails the two 3-year growth rules, passes the rest."""
    return fundamentals(
        name="Slow Corp",
        annual=[{"period": f"{y}-12-31", "revenue": r, "net_income": n, "fcf": 20e9}
                for y, r, n in ((2025, 100e9, 20e9), (2024, 95e9, 19e9), (2023, 90e9, 18e9), (2022, 86e9, 17e9))],
    )


SLOW = Security("SLOW", "US", "Slow Corp")
STALE_DATA = fundamentals(quarterly=[q("2025-03-31", 90e9, 22e9, 26e9), q("2024-03-31", 80e9, 20e9, 24e9)])


def news(n, summary=""):
    from datetime import datetime

    return [report.Item(f"k{i}", "filing" if summary else "news", f"Headline number {i}", "http://x", "Reuters",
                        datetime(2026, 11, 10), summary) for i in range(n)]


def explain(eng, monkeypatch, sec, data, updates=(), thesis=False):
    run(eng.lock_blueprint())
    if thesis:
        eng.store.replace_holdings([Holding(sec, "portfolio", 10, 150, ("pg1",), "HIGH")])
        eng.blocks["pg1"] = thesis_blocks()

    def gather(s, *a, **k):
        r = research(s, data=data)
        r.updates = list(updates)
        return r

    monkeypatch.setattr(report, "gather", gather)
    c = run(eng.conviction(sec))
    return c, cv.explain_data(c)


def test_explain_data_for_a_stock_failing_two_rules_lists_only_those_and_counts_the_rest(eng, monkeypatch):
    c, data = explain(eng, monkeypatch, SLOW, slow_grower())
    assert "[FAIL] Profit grew at least 10% a year over 3 years (Profit CAGR 3Y ≥ 10.0%): " in data
    assert "[FAIL] Revenue grew at least 10% a year over 3 years" in data
    assert f"(the other {c.verdict.passed} rules pass; not listed)" in data
    assert "Return on equity at least 15%" not in data and "Debt/equity at most 1" not in data  # passing rules
    assert "VERDICT: AVOID" in data and "The investor does not hold this stock." in data
    assert "THESIS: none written." in data
    assert data.count("judgment rules NOT yet confirmed") == 1 and "I understand how the company makes money (must pass)" in data
    assert "Latest quarter:" in data and "Jun 2026" not in data  # only the newest quarter
    assert "FY ending" not in data and "Price:" not in data and "Market cap" not in data


def test_explain_data_for_a_held_stock_carries_the_thesis_in_short(eng, monkeypatch):
    c, data = explain(eng, monkeypatch, GOOGL, fundamentals(), thesis=True)
    assert "The investor holds this stock." in data and "VERDICT: HOLD" in data
    assert "THESIS (written by the investor):" in data and "Search is still a cash machine" in data
    for key in ("thesis", "pillars", "risks", "breakers", "plan"):
        assert sum(ln.startswith(f"  {cv.TITLES[key]}:") for ln in data.splitlines()) <= 3


def test_explain_data_for_a_loss_maker_shows_why_it_fails(eng, monkeypatch):
    c, data = explain(eng, monkeypatch, JUNK, LOSS_MAKER)
    assert "[FAIL]" in data and "VERDICT: AVOID" in data
    assert "Return on equity at least 15%" in data  # failing, so it is listed


def test_explain_data_keeps_the_out_of_date_warning(eng, monkeypatch):
    c, data = explain(eng, monkeypatch, SLOW, STALE_DATA)
    assert "WARNING: the latest quarter available is Mar 2025" in data


def test_explain_data_reads_five_headlines_and_cuts_filing_text(eng, monkeypatch):
    c, data = explain(eng, monkeypatch, SLOW, slow_grower(), updates=news(9, summary="x" * 400))
    assert data.count("Headline number") == cv.EXPLAIN_NEWS == 5 and "Headline number 4" in data
    assert "Headline number 5" not in data and "x" * 121 not in data and "x" * 120 in data


def test_explain_data_stays_short_in_the_worst_case(eng, monkeypatch):
    """About 4 characters a token and 20 tokens a second on the server: this is the bound on reading time."""
    c, data = explain(eng, monkeypatch, GOOGL, fundamentals(), updates=news(12, summary="y" * 300), thesis=True)
    assert len(data) < 2800


def test_the_explanation_prompt_asks_for_a_short_reply_and_caps_it(eng, monkeypatch):
    c, _ = explain(eng, monkeypatch, SLOW, slow_grower())
    run(eng.conviction_summary(c))
    from stockbot.engine import WHY_MAX_TOKENS

    assert eng.llm.last_max_tokens == WHY_MAX_TOKENS == 150
    assert "at most 70 words" in eng.llm.last_messages[0]["content"]


def test_explanation_is_cached_by_what_the_model_reads(eng, monkeypatch):
    c, _ = explain(eng, monkeypatch, SLOW, slow_grower(), updates=news(2))
    first = run(eng.conviction_summary(c))
    assert run(eng.conviction_summary(c)) == first and eng.llm.calls == 1
    c, _ = explain(eng, monkeypatch, SLOW, {**slow_grower(), "price": 999.0}, updates=news(2))  # price isn't read
    assert run(eng.conviction_summary(c)) == first and eng.llm.calls == 1
    c, _ = explain(eng, monkeypatch, SLOW, slow_grower(), updates=news(3))  # a new headline is
    run(eng.conviction_summary(c))
    assert eng.llm.calls == 2


def test_a_failed_or_empty_explanation_is_not_cached(eng, monkeypatch):
    c, _ = explain(eng, monkeypatch, SLOW, slow_grower())
    eng.llm = FakeLLM(fail=True)
    assert run(eng.conviction_summary(c)).startswith("(The local model is unavailable")
    eng.llm = FakeLLM("")
    assert run(eng.conviction_summary(c)) == "" and eng.llm.calls == 1
    eng.llm = FakeLLM("It fails the growth rules. Watch next quarter.")
    assert run(eng.conviction_summary(c)) == "It fails the growth rules. Watch next quarter."
    assert eng.llm.calls == 1  # the model was asked again: neither failure was kept
    assert run(eng.conviction_summary(c)) and eng.llm.calls == 1  # now it is


def test_the_cache_key_follows_the_model(eng, monkeypatch):
    c, _ = explain(eng, monkeypatch, SLOW, slow_grower())
    run(eng.conviction_summary(c))
    eng.settings = make_settings(ollama_model="other", notion_blueprint_db=BLUEPRINT_DB)
    run(eng.conviction_summary(c))
    assert eng.llm.calls == 2


# --- the reply cap (llm/client.py) ---

@pytest.mark.parametrize("text, kept", [
    ("ROE is 24.3% today. Revenue grew and", "ROE is 24.3% today."),
    ("It fails. Profit fell 5.3%. Watch the next", "It fails. Profit fell 5.3%."),
    ("Margins are 18.2", "Margins are 18.2"),  # no complete sentence: keep what there is
    ('He said "it fails." Then', 'He said "it fails."'),
])
def test_a_reply_cut_by_the_cap_loses_only_its_unfinished_sentence(text, kept):
    from stockbot.llm.client import drop_partial_sentence

    assert drop_partial_sentence(text) == kept


def test_llm_sends_the_cap_and_trims_only_a_capped_reply():
    from stockbot.llm.client import LLM

    sent = []

    def client(content, done_reason):
        async def chat(**kw):
            sent.append(kw["options"])
            return SimpleNamespace(message=SimpleNamespace(content=content), done_reason=done_reason)

        return SimpleNamespace(chat=chat)

    llm = LLM("http://x", "m", 8192)
    llm.client = client("One. Two and", "length")
    assert run(llm.chat([], max_tokens=150)) == "One." and sent[-1]["num_predict"] == 150
    llm.client = client("One. Two and", "stop")  # finished by itself: leave it alone
    assert run(llm.chat([], max_tokens=150)) == "One. Two and"
    llm.client = client("One. Two and", "length")
    assert run(llm.chat([])) == "One. Two and" and "num_predict" not in sent[-1]  # no cap, no trimming
