import json
from pathlib import Path

import pytest

from stockbot.alerts.digest import Position, portfolio_line, totals, totals_lines, wishlist_line
from stockbot.data import notion
from stockbot.data.tickers import Resolver
from stockbot.models import Holding, Security
from stockbot.store import Store
from stockbot.sync import build_holdings

from .test_data import UNIVERSE

FIX = Path(__file__).parent / "fixtures"
RIL = Security("RELIANCE", "IN", "Reliance Industries Limited")
AAPL = Security("AAPL", "US", "Apple Inc.", 320193)
TCS = Security("TCS", "IN", "Tata Consultancy Services Limited")


def rows(name, list_type, overrides=None):
    return notion.parse_rows(json.loads((FIX / name).read_text())["results"], list_type, overrides)


@pytest.mark.parametrize(
    "link",
    [
        "https://www.notion.so/myspace/Portfolio-1a2b3c4d5e6f40718293a4b5c6d7e8f9?v=0123456789abcdef0123456789abcdef",
        "https://www.notion.so/1a2b3c4d5e6f40718293a4b5c6d7e8f9",
        "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
        # A title ending in hex letters runs into the id once dashes are dropped.
        "https://www.notion.so/My-Feed-1a2b3c4d5e6f40718293a4b5c6d7e8f9",
    ],
)
def test_db_id(link):
    assert notion.db_id(link) == "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9"


def test_parse_portfolio_columns_and_types():
    r = rows("notion_portfolio.json", "portfolio")
    assert [x.ticker for x in r] == ["RELIANCE", "INFY", "AAPL"]  # empty and trashed rows dropped
    assert r[0].name == "Reliance Industries" and r[0].exchange == "IN"
    assert (r[0].qty, r[0].avg_price) == (10, 1200.5)
    assert (r[1].qty, r[1].avg_price, r[1].exchange) == (5, 1450, None)  # formula qty, text price
    assert (r[2].exchange, r[2].qty) == ("US", 2.5)


def test_parse_wishlist_detects_other_column_names():
    r = rows("notion_wishlist.json", "wishlist")
    assert r[0].name == "Tata Consultancy Services" and r[0].ticker is None and r[0].exchange == "IN"
    assert r[1].ticker == "QQQZZ"


def test_column_override():
    r = rows("notion_wishlist.json", "wishlist", {"ticker": "Company"})
    assert r[0].ticker == "TATA CONSULTANCY SERVICES"


def test_build_holdings_matches_ticker_then_name():
    resolver = Resolver(UNIVERSE)
    rep = build_holdings(resolver, rows("notion_portfolio.json", "portfolio") + rows("notion_wishlist.json", "wishlist"))
    got = {(h.sec.symbol, h.sec.market, h.list_type) for h in rep.holdings}
    # INFY is on NSE and NYSE and both names are "Infosys": the row is reported, not guessed.
    assert got == {("RELIANCE", "IN", "portfolio"), ("AAPL", "US", "portfolio"), ("TCS", "IN", "wishlist")}
    unmatched = {row.label: why for row, why in rep.unmatched}
    assert unmatched["INFY"].startswith("ambiguous")
    assert unmatched["QQQZZ"].startswith("no listed stock matches")


def test_replace_holdings_reports_changes_and_merges():
    store = Store(":memory:")
    added, removed = store.replace_holdings([Holding(RIL, "portfolio", 10, 100), Holding(TCS, "wishlist")])
    assert set(added) == {RIL, TCS} and removed == []
    added, removed = store.replace_holdings(
        [
            Holding(RIL, "portfolio", 10, 100),
            Holding(RIL, "portfolio", 30, 200),  # a second lot
            Holding(AAPL, "wishlist"),
            Holding(AAPL, "portfolio", 1, 150),  # in both lists: portfolio wins
        ]
    )
    assert added == [AAPL] and removed == [TCS]
    by = {h.sec.symbol: h for h in store.holdings()}
    assert (by["RELIANCE"].qty, by["RELIANCE"].avg_price) == (40, 175)
    assert by["AAPL"].list_type == "portfolio"


def test_position_pnl_and_totals():
    ril = Position(Holding(RIL, "portfolio", 10, 1000), price=1100, prev=1000, high_52w=1200)
    aapl = Position(Holding(AAPL, "portfolio", 2, 300), price=330, prev=300, high_52w=350)
    no_cost = Position(Holding(TCS, "portfolio", 4, None), price=2500, prev=2500, high_52w=3000)
    assert (ril.value, ril.day_value, ril.invested, ril.pnl) == (11000, 1000, 10000, 1000)
    t = totals([ril, aapl, no_cost])
    assert t["INR"] == {"value": 21000, "day": 1000, "invested": 10000, "pnl_value": 11000}
    assert t["USD"]["value"] == 660 and t["USD"]["invested"] == 600
    assert "P&L +₹1,000 (+10.0%)" in portfolio_line(ril)
    assert "P&L" not in portfolio_line(no_cost)
    lines = totals_lines([ril, aapl])
    assert lines[0] == "<b>Total INR</b> ₹11,000 · day +₹1,000 (+10.0%) · P&L +₹1,000 (+10.0%)"
    assert lines[1].startswith("<b>Total USD</b> $660.00 · day +$60.00 (+10.0%) · P&L +$60.00 (+10.0%)")


def test_wishlist_line():
    p = Position(Holding(TCS, "wishlist"), price=2400, prev=2500, high_52w=3000)
    assert wishlist_line(p) == "⭐ <b>TCS</b> ₹2,400.00 (-4.0%) · -20.0% from 52w high"


def test_query_pages_follows_data_sources_and_cursors(monkeypatch):
    import httpx

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers["Notion-Version"]))
        if request.url.path == "/v1/databases/1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9":
            return httpx.Response(200, json={"data_sources": [{"id": "ds1", "name": "Portfolio"}]})
        body = json.loads(request.content)
        if "start_cursor" not in body:
            return httpx.Response(200, json={"results": [{"id": "p1"}], "has_more": True, "next_cursor": "c2"})
        assert body["start_cursor"] == "c2"
        return httpx.Response(200, json={"results": [{"id": "p2"}], "has_more": False, "next_cursor": None})

    real = httpx.Client
    monkeypatch.setattr(notion.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    pages = notion.query_pages("secret", "1a2b3c4d5e6f40718293a4b5c6d7e8f9")
    assert [p["id"] for p in pages] == ["p1", "p2"]
    assert calls[1] == ("POST", "/v1/data_sources/ds1/query", notion.VERSION)


def test_query_pages_explains_a_missing_connection(monkeypatch):
    import httpx

    real = httpx.Client
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"code": "object_not_found"}))
    monkeypatch.setattr(notion.httpx, "Client", lambda **kw: real(transport=transport, **kw))
    with pytest.raises(PermissionError, match="Connections"):
        notion.query_pages("secret", "1a2b3c4d5e6f40718293a4b5c6d7e8f9")


def test_unknown_ticker_is_not_fuzzy_matched():
    from stockbot.sync import match

    resolver = Resolver(UNIVERSE)
    row = notion.NotionRow("wishlist", "QQQZZ", "Some Unlisted Startup", None, None, None, "u")
    sec, why = match(resolver, row)
    assert sec is None and why.startswith("no listed stock matches")
    ok, _ = match(resolver, notion.NotionRow("portfolio", None, "Apple", "US", 1, 1, "u"))
    assert ok.symbol == "AAPL"


def test_etf_found_by_exact_lookup_and_default_market():
    from stockbot.sync import match

    resolver = Resolver(UNIVERSE)
    calls = []

    def lookup(symbol, market):
        calls.append((symbol, market))
        return Security("QQQM", "US", "Invesco NASDAQ 100 ETF") if symbol == "QQQM" else None

    pages = [
        {"url": "u1", "properties": {"Name": {"type": "title", "title": [{"plain_text": "QQQM"}]},
                                     "Ticker": {"type": "rich_text", "rich_text": [{"plain_text": "QQQM"}]}}},
        {"url": "u2", "properties": {"Name": {"type": "title", "title": [{"plain_text": "INFY"}]},
                                     "Ticker": {"type": "rich_text", "rich_text": [{"plain_text": "INFY"}]}}},
    ]
    qqqm, infy = notion.parse_rows(pages, "portfolio", default_market="US")
    assert qqqm.exchange == infy.exchange == "US"
    sec, _ = match(resolver, qqqm, lookup)
    assert sec.name == "Invesco NASDAQ 100 ETF" and calls == [("QQQM", "US")]
    # The database's market settles a ticker listed on both NSE and NYSE, without a lookup.
    sec, _ = match(resolver, infy, lookup)
    assert (sec.symbol, sec.market) == ("INFY", "US") and len(calls) == 1
