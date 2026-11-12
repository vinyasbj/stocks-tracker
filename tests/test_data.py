import json
from pathlib import Path

from stockbot.data import filings_india, filings_us, news, regulators
from stockbot.data.tickers import Resolver, clean_name
from stockbot.models import Security

FIX = Path(__file__).parent / "fixtures"

UNIVERSE = [
    Security("RELIANCE", "IN", "Reliance Industries Limited"),
    Security("HDFCBANK", "IN", "HDFC Bank Limited"),
    Security("HDFCLIFE", "IN", "HDFC Life Insurance Company Limited"),
    Security("INFY", "IN", "Infosys Limited"),
    Security("TCS", "IN", "Tata Consultancy Services Limited"),
    Security("TATAMOTORS", "IN", "Tata Motors Limited"),
    Security("AAPL", "US", "Apple Inc.", 320193),
    Security("INFY", "US", "Infosys Ltd", 1067491),
    Security("HDB", "US", "HDFC BANK LTD", 1144967),
    Security("BRK.B", "US", "BERKSHIRE HATHAWAY INC", 1067983),
    Security("STHO", "US", "Star Holdings", 1953366),
]


def test_clean_name():
    assert clean_name("Reliance Industries Limited") == "Reliance Industries"
    assert clean_name("Apple Inc.") == "Apple"


def test_resolve_symbols_and_names():
    r = Resolver(UNIVERSE)
    assert [s.symbol for s in r.resolve("reliance")] == ["RELIANCE"]
    assert [s.symbol for s in r.resolve("AAPL")] == ["AAPL"]
    assert [s.symbol for s in r.resolve("tata consultancy")] == ["TCS"]
    assert r.resolve("brk-b")[0].yf_symbol == "BRK-B"
    # Listed in both markets: ask, unless the user names the market.
    assert {(s.symbol, s.market) for s in r.resolve("INFY")} == {("INFY", "IN"), ("INFY", "US")}
    assert [(s.symbol, s.market) for s in r.resolve("INFY.NS")] == [("INFY", "IN")]
    assert [(s.symbol, s.market) for s in r.resolve("US:infy")] == [("INFY", "US")]
    # An NSE listing and its ADR tie on name; unrelated HDFC companies are not offered.
    assert {s.symbol for s in r.resolve("hdfc bank")} == {"HDFCBANK", "HDB"}
    assert r.resolve("zzzz qqqq") == []


def test_nse_parser_tags_results_and_routine():
    items = filings_india.parse_nse(json.loads((FIX / "nse_announcements.json").read_text()), 10)
    by_key = {i.key: i for i in items}
    assert "routine" in by_key["nse:106838897"].tags  # analyst-call transcript
    assert by_key["nse:106800001"].tags == {"results"}
    assert by_key["nse:106790000"].title.startswith("Updates: Reliance Industries Limited has informed")
    assert "routine" in by_key["nse:106780000"].tags
    assert by_key["nse:106800001"].published.isoformat().startswith("2026-10-30T16:01")


def test_edgar_parser():
    items = filings_us.parse_submissions(json.loads((FIX / "edgar_submissions.json").read_text()), 320193, 10)
    assert [i.title for i in items] == ["10-K annual report", "8-K: Results of operations (earnings)", "8-K: Director/officer change"]
    assert items[1].tags == {"results"}
    assert items[1].url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000024/aapl-20261102.htm"


def test_regulator_feeds():
    sebi = regulators.parse_feed("SEBI", (FIX / "sebi.xml").read_bytes())
    assert sebi[0].url == "https://www.sebi.gov.in/sebi_data/attachdocs/nov-2026/exemption_order_embil.pdf"
    assert sebi[0].published is not None
    rbi = regulators.parse_feed("RBI press", (FIX / "rbi_press.xml").read_bytes())
    assert "routine" in rbi[0].tags and "routine" not in rbi[1].tags


def test_google_news_strips_source_and_drops_quote_pages():
    items = news.parse_google_news((FIX / "google_news.xml").read_bytes(), 10)
    assert [i.title for i in items] == ["TCS Q2 revenue climbs 2.8% as AI business tops $3B"]
    assert items[0].source == "Staffing Industry Analysts"


def test_fundamentals_keep_description_and_country_as_text(monkeypatch):
    import pandas as pd

    from stockbot.data import market
    from stockbot.models import Security
    from stockbot.store import Store

    class FakeTicker:
        info = {
            "longName": "Alphabet Inc.",
            "longBusinessSummary": "Alphabet operates through Google Services, Google Cloud, and Other Bets segments.",
            "country": "United States",
            "currentPrice": "170.2",
            "trailingPE": 24.1,
        }
        quarterly_income_stmt = income_stmt = balance_sheet = cashflow = pd.DataFrame()

        def __init__(self, symbol):
            pass

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)
    f = market.fetch_fundamentals(Security("GOOGL", "US", "Alphabet Inc."), Store(":memory:"))
    assert f["business_summary"].startswith("Alphabet operates through Google Services")
    assert f["country"] == "United States"
    assert f["price"] == 170.2 and f["pe"] == 24.1  # numbers are still converted


# --- industry peers stay in the stock's own market ---

class _FakeTicker:
    INFO = {
        "HDB": {"country": "India", "earningsGrowth": 0.20},
        "IBN": {"country": "India", "earningsGrowth": 0.25},
        "USB": {"country": "United States", "earningsGrowth": 0.05},
        "PNC": {"country": "United States", "earningsGrowth": 0.07},
        "MFG": {"country": "Japan", "earningsGrowth": None},
    }

    def __init__(self, sym):
        self.info = self.INFO.get(sym, {"country": "United States", "earningsGrowth": 0.01})


def test_us_peers_leave_out_indian_adrs_and_preferred_shares(monkeypatch):
    from stockbot.data import market
    from stockbot.store import Store

    quotes = [{"symbol": s, "quoteType": "EQUITY"} for s in ["HDB", "IBN", "MFG", "USB", "USB-PH", "PNC"]]
    monkeypatch.setattr(market.yf, "screen", lambda q, **kw: {"quotes": quotes})
    monkeypatch.setattr(market.yf, "Ticker", _FakeTicker)
    peers = market.peer_eps_growth("Banks—Regional", "US", Store(":memory:"))
    assert peers == {"USB": 0.05, "PNC": 0.07}  # no HDB/IBN (Indian), no USB-PH (preferred), MFG has no figure


def test_peer_query_uses_only_the_stocks_own_exchanges():
    from stockbot.data import market

    def parts(market_code):
        region, industry, exchanges = market._peer_query("Banks—Regional", market_code).to_dict()["operands"]
        return region["operands"], industry["operands"], [e["operands"][1] for e in exchanges["operands"]]

    assert parts("IN") == (["region", "in"], ["industry", "Banks—Regional"], ["NSI"])
    region, _, exchanges = parts("US")
    assert region == ["region", "us"] and exchanges == ["NYQ", "NMS", "NGM", "NCM", "ASE"]  # no OTC (PNK)


def test_where_peers_come_from():
    from stockbot.data import market

    assert market.peer_market(Security("TCS", "IN", "TCS"), {"country": "India"}) == "IN"
    assert market.peer_market(Security("INFY", "US", "Infosys"), {"country": "India"}) == "IN"  # the ADR
    assert market.peer_market(Security("MSFT", "US", "Microsoft"), {"country": "United States"}) == "US"
    assert market.screener_industry("Banks - Regional") == "Banks—Regional"
    assert market.screener_industry("Made-up industry") is None
