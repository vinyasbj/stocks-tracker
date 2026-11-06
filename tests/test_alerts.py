from stockbot.alerts.monitor import mentions, new_items, regulator_candidates
from stockbot.analysis.report import clip
from stockbot.engine import plain_text
from stockbot.models import Holding, Item, Security
from stockbot.store import Store

HDFC = Security("HDFCBANK", "IN", "HDFC Bank Limited")
RIL = Security("RELIANCE", "IN", "Reliance Industries Limited")


def item(key, title="t", source="NSE", tags=None):
    return Item(key, "news", title, "", source, tags=tags or set())


def test_first_run_is_a_baseline_then_only_new_items_come_back():
    store = Store(":memory:")
    assert new_items(store, "s", [item("a"), item("b")]) is None
    assert new_items(store, "s", [item("a"), item("b")]) == []
    fresh = new_items(store, "s", [item("c"), item("a"), item("d", tags={"routine"})])
    assert [i.key for i in fresh] == ["c"]
    assert new_items(store, "s", [item("c"), item("d")]) == []  # routine items are still marked seen


def test_history_keeps_last_turns():
    store = Store(":memory:")
    for n in range(8):
        store.add_turn(1, "user", f"q{n}")
    assert [t["content"] for t in store.history(1, 3)] == ["q5", "q6", "q7"]


def test_regulator_prefilter():
    bank_fund = {"sector": "Financial Services", "industry": "Banks - Regional"}
    oil_fund = {"sector": "Energy", "industry": "Oil & Gas Refining & Marketing"}
    stocks = [(Holding(HDFC, "portfolio"), bank_fund), (Holding(RIL, "wishlist"), oil_fund)]
    rbi = item("r", "RBI revises capital norms", source="RBI notification")
    assert [h.sec.symbol for h, _ in regulator_candidates(rbi, stocks)] == ["HDFCBANK"]
    sebi_named = item("s", "Order in the matter of Reliance Industries Limited", source="SEBI")
    assert {h.sec.symbol for h, _ in regulator_candidates(sebi_named, stocks)} == {"HDFCBANK", "RELIANCE"}
    sec_other = item("x", "SEC charges hedge fund", source="SEC")
    assert regulator_candidates(sec_other, stocks) == []


def test_mentions_needs_whole_symbol():
    assert mentions(item("a", "TCS wins $1B deal"), Security("TCS", "IN", "Tata Consultancy Services Limited"))
    assert not mentions(item("a", "Stocks rally"), Security("TCS", "IN", "Tata Consultancy Services Limited"))


def test_plain_text_and_clip():
    assert plain_text("## Head\n**Revenue** rose") == "Head\nRevenue rose"
    long = "\n".join(["<b>line</b>"] * 1000)
    out = clip(long, 100)
    assert len(out) <= 102 and out.endswith("…") and out.count("<b>") == out.count("</b>")
