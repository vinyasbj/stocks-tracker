import json
from pathlib import Path

import pytest

from stockbot.alerts.digest import Position, portfolio_line, totals, totals_lines, wishlist_line
from stockbot.data import market, notion
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


def test_parse_keeps_page_id_and_conviction():
    r = rows("notion_portfolio.json", "portfolio")
    assert r[0].page_id == "11111111-2222-3333-4444-555555555555" and r[0].conviction == "HIGH"
    assert (r[1].page_id, r[1].conviction) == ("", None)


@pytest.mark.parametrize("cell,level", [("High", "HIGH"), ("med", "MEDIUM"), ("Medium", "MEDIUM"), ("LOW", "LOW"), ("", None), ("unsure", None)])
def test_conviction_level(cell, level):
    assert notion.conviction_level(cell) == level


def test_find_columns_with_other_candidates():
    props = {"Criterion": {}, "Must pass?": {}, "Value": {}}
    cols = notion.find_columns(props, {"rule": ["rule", "criterion"], "must": ["must pass"], "threshold": ["threshold", "value"]})
    assert cols == {"rule": "Criterion", "must": "Must pass?", "threshold": "Value"}


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


def test_holdings_keep_pages_and_conviction():
    store = Store(":memory:")
    store.replace_holdings(
        [
            Holding(RIL, "portfolio", 10, 100, ("p1",), None),
            Holding(RIL, "portfolio", 30, 200, ("p2",), "HIGH"),  # a second lot, with the thesis
            Holding(TCS, "wishlist"),
            Holding(AAPL, "portfolio", None, None, ("p3",)),
            Holding(AAPL, "portfolio", None, None, ("p4",)),  # lots without quantities still merge pages
        ]
    )
    by = {h.sec.symbol: h for h in store.holdings()}
    assert by["RELIANCE"].pages == ("p1", "p2") and by["RELIANCE"].conviction == "HIGH"
    assert by["RELIANCE"].qty == 40
    assert by["AAPL"].pages == ("p3", "p4")
    assert by["TCS"].pages == () and by["TCS"].conviction is None
    assert store.holding(RIL) == by["RELIANCE"] and store.holding(Security("X", "US", "X")) is None


def test_wishlist_rows_carry_no_page():
    resolver = Resolver(UNIVERSE)
    portfolio = [notion.NotionRow("portfolio", "AAPL", None, "US", 1, 100, "u1", "page-a", "LOW")]
    wishlist = [notion.NotionRow("wishlist", "TCS", None, "IN", None, None, "u2", "page-b", "HIGH")]
    rep = build_holdings(resolver, portfolio + wishlist)
    by = {h.sec.symbol: h for h in rep.holdings}
    assert by["AAPL"].pages == ("page-a",) and by["AAPL"].conviction == "LOW"
    assert by["TCS"].pages == () and by["TCS"].conviction is None


def test_store_migrates_an_old_holdings_table(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE holdings (symbol TEXT NOT NULL, market TEXT NOT NULL, name TEXT NOT NULL, cik INTEGER, "
        "list_type TEXT NOT NULL, qty REAL, avg_price REAL, PRIMARY KEY (symbol, market))"
    )
    db.execute("INSERT INTO holdings VALUES ('RELIANCE', 'IN', 'Reliance Industries Limited', NULL, 'portfolio', 5, 100)")
    db.commit()
    db.close()
    store = Store(path)
    assert store.holdings() == [Holding(RIL, "portfolio", 5, 100)]
    Store(path)  # opening again must not fail on the existing columns
    store.replace_holdings([Holding(RIL, "portfolio", 5, 100, ("p1",))])
    assert store.holdings()[0].pages == ("p1",)


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


def test_sold_portfolio_rows_are_dropped_for_deletion():
    resolver = Resolver(UNIVERSE)
    sold = notion.NotionRow("portfolio", "AAPL", None, "US", 0, 100, "u1", "page-a")
    no_qty = notion.NotionRow("portfolio", "TCS", None, "IN", None, None, "u2", "page-b")
    wish = notion.NotionRow("wishlist", "INFY", None, "IN", 0, None, "u3", "page-c")
    rep = build_holdings(resolver, [sold, no_qty, wish])
    assert rep.sold == [sold]
    assert {h.sec.symbol for h in rep.holdings} == {"TCS", "INFY"}


def test_portfolio_pages_without_icon_need_a_logo():
    resolver = Resolver(UNIVERSE)
    bare = notion.NotionRow("portfolio", "AAPL", None, "US", 1, 100, "u1", "page-a")
    iconed = notion.NotionRow("portfolio", "TCS", None, "IN", 1, 100, "u2", "page-b", has_icon=True)
    wish = notion.NotionRow("wishlist", "INFY", None, "IN", None, None, "u3", "page-c")
    rep = build_holdings(resolver, [bare, iconed, wish])
    assert [(p, sec.symbol) for p, sec in rep.need_logo] == [("page-a", "AAPL")]


def test_logo_url_from_website_or_fund_family():
    from stockbot.data.market import logo_url

    assert logo_url({"website": "https://www.shopify.com"}) == "https://www.google.com/s2/favicons?domain=shopify.com&sz=128"
    assert logo_url({"website": None, "fund_family": "Global X Funds"}).endswith("domain=globalxetfs.com&sz=128")
    assert logo_url({"fund_family": "Unknown AMC"}) is None


def test_update_page_patches_the_page(monkeypatch):
    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    real = httpx.Client
    monkeypatch.setattr(notion.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    notion.update_page("secret", "page-a", {"in_trash": True})
    assert seen == [("PATCH", "/v1/pages/page-a", {"in_trash": True})]


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


def _mock_notion(monkeypatch, handler):
    import httpx

    real = httpx.Client
    monkeypatch.setattr(notion.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _block(id_, type_, text="", children=False):
    return {"object": "block", "id": id_, "type": type_, "has_children": children,
            type_: {"rich_text": [{"plain_text": text}]}}


def test_page_blocks_paginates_and_flattens_children_in_order(monkeypatch):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        path, cursor = request.url.path, request.url.params.get("start_cursor")
        if path == "/v1/blocks/page1/children" and cursor is None:
            return httpx.Response(200, json={"results": [_block("h1", "heading_2", "Why I own it"),
                                                         _block("t1", "toggle", "Pillars", children=True)],
                                             "has_more": True, "next_cursor": "c2"})
        if path == "/v1/blocks/page1/children":
            assert cursor == "c2"
            return httpx.Response(200, json={"results": [_block("p9", "paragraph", "last")], "has_more": False})
        if path == "/v1/blocks/t1/children":
            return httpx.Response(200, json={"results": [_block("b1", "bulleted_list_item", "Search", children=True)],
                                             "has_more": False})
        if path == "/v1/blocks/b1/children":
            return httpx.Response(200, json={"results": [_block("b2", "bulleted_list_item", "nested")], "has_more": False})
        raise AssertionError(path)

    _mock_notion(monkeypatch, handler)
    assert [b["id"] for b in notion.page_blocks("secret", "page1")] == ["h1", "t1", "b1", "b2", "p9"]


def test_page_blocks_stops_at_depth(monkeypatch):
    import httpx

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        n = len(calls)
        return httpx.Response(200, json={"results": [_block(f"b{n}", "toggle", children=True)], "has_more": False})

    _mock_notion(monkeypatch, handler)
    assert len(notion.page_blocks("secret", "page1", depth=3)) == 3
    assert len(calls) == 3


def test_rate_limited_requests_are_retried_after_the_wait(monkeypatch):
    import httpx

    waits, replies = [], [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={"results": [], "has_more": False})]
    monkeypatch.setattr(notion.time, "sleep", waits.append)
    _mock_notion(monkeypatch, lambda r: replies.pop(0))
    assert notion.page_blocks("secret", "page1") == []
    assert waits == [2.0]


def test_page_not_shared_explains_the_connection(monkeypatch):
    import httpx

    _mock_notion(monkeypatch, lambda r: httpx.Response(404, json={"code": "object_not_found"}))
    with pytest.raises(PermissionError, match="add your integration"):
        notion.page_blocks("secret", "page1")


@pytest.mark.parametrize("value", ["", "https://www.notion.so/...", "   "])
def test_placeholder_notion_links_count_as_unset(monkeypatch, value):
    from stockbot.config import notion_link

    monkeypatch.setenv("NOTION_WISHLIST_DB", value)
    assert notion_link("NOTION_WISHLIST_DB") == ""


def test_real_notion_link_is_kept(monkeypatch):
    from stockbot.config import notion_link

    link = "https://www.notion.so/My-Wishlist-1a2b3c4d5e6f40718293a4b5c6d7e8f9?v=0123"
    monkeypatch.setenv("NOTION_WISHLIST_DB", link)
    assert notion_link("NOTION_WISHLIST_DB") == link


def test_sync_with_only_a_portfolio(monkeypatch, settings):
    import asyncio

    from stockbot.engine import Engine

    queried = []

    def fake_query(token, database):
        queried.append(database)
        return json.loads((FIX / "notion_portfolio.json").read_text())["results"]

    writes = []
    monkeypatch.setattr(notion, "query_pages", fake_query)
    monkeypatch.setattr(notion, "update_page", lambda token, page, body: writes.append(page))
    monkeypatch.setattr(market, "fetch_fundamentals", lambda sec, store: {"website": "https://www.example.com"})
    eng = Engine(settings, Store(":memory:"))
    eng._resolver = Resolver(UNIVERSE)
    eng._lookup = lambda symbol, mkt: None
    rep = asyncio.run(eng.sync(write_notion=False))  # what /report does
    assert queried == [settings.notion_portfolio_db]
    assert {h.sec.symbol for h in rep.holdings} == {"RELIANCE", "AAPL"}
    assert writes == []
    rep = asyncio.run(eng.sync())
    assert writes == [page for page, _ in rep.need_logo] and writes  # the fixture pages have no icon


def test_settings_read_the_blueprint_database(monkeypatch):
    from stockbot import config

    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("NOTION_BLUEPRINT_DB", "https://www.notion.so/Blueprint-1a2b3c4d5e6f40718293a4b5c6d7e8f9?v=1")
    monkeypatch.setenv("NOTION_WISHLIST_DB", "https://www.notion.so/...")
    s = config.load_settings()
    assert s.notion_blueprint_db.endswith("?v=1") and s.notion_wishlist_db == ""
    monkeypatch.setenv("NOTION_BLUEPRINT_DB", "")
    assert config.load_settings().notion_blueprint_db == ""
