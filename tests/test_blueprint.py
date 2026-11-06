import json
from pathlib import Path

from stockbot.analysis import blueprint as bp
from stockbot.analysis.blueprint import Blueprint, Rule

FIX = Path(__file__).parent / "fixtures"


def parsed():
    return bp.parse_rules(json.loads((FIX / "notion_blueprint.json").read_text())["results"])


def by_text(rules):
    return {r.text: r for r in rules}


def test_numeric_and_judgment_rules():
    p = parsed()
    rules = by_text(p.blueprint.rules)
    assert rules["Return on equity at least 15%"] == Rule("Return on equity at least 15%", "Quality", "roe", ">=", 15, True)
    assert rules["Debt/equity at most 1"].metric == "debt_to_equity" and rules["Debt/equity at most 1"].op == "<="
    assert rules["P/E at most 40"].metric == "pe"
    assert rules["Net profit margin at least 8%"].op == ">=" and not rules["Net profit margin at least 8%"].must
    loss = rules["Profit-making: not loss-making in each of the last 2 years"]
    assert (loss.metric, loss.op, loss.threshold, loss.must) == ("loss_years", "<=", 1, True)
    assert rules["I understand how the company makes money"].is_judgment
    assert rules["Moat"].is_judgment and rules["Moat"].pillar == "Business"  # empty metric, lower-case pillar


def test_settings_row_sets_the_pass_bar():
    assert parsed().blueprint.pass_bar == 70
    assert bp.parse_rules([]).blueprint.pass_bar == 75


def test_unusable_rows_are_reported_not_guessed():
    p = parsed()
    problems = "\n".join(p.problems)
    assert "Promoter holding at least 50%: unknown metric 'Promoter holding'" in problems
    assert "ROE between 15 and 30: condition must be ≥ or ≤" in problems
    assert "Revenue CAGR at least 10%: threshold is missing" in problems
    assert "looks like a fraction; write 15 for 15%" in problems
    assert len(p.problems) == 4
    names = {r.text for r in p.blueprint.rules}
    assert "Old rule in the trash" not in names and "" not in names
    assert len(p.blueprint.rules) == 7


def test_text_typed_columns_from_a_csv_import():
    page = {"properties": {
        "Rule": {"type": "title", "title": [{"plain_text": "ROE at least 15%"}]},
        "Metric": {"type": "rich_text", "rich_text": [{"plain_text": "Return on equity"}]},
        "Condition": {"type": "rich_text", "rich_text": [{"plain_text": ">="}]},
        "Threshold": {"type": "rich_text", "rich_text": [{"plain_text": "15"}]},
        "Must pass": {"type": "rich_text", "rich_text": [{"plain_text": "Yes"}]},
        "Pillar": {"type": "rich_text", "rich_text": [{"plain_text": "Quality"}]},
    }}
    p = bp.parse_rules([page])
    assert p.problems == []
    assert p.blueprint.rules == [Rule("ROE at least 15%", "Quality", "roe", ">=", 15, True)]


def test_metric_aliases():
    assert bp.metric_key("Return on equity") == "roe"
    assert bp.metric_key("d/e") == "debt_to_equity"
    assert bp.metric_key("Profit growth YoY") == "ni_yoy"
    assert bp.metric_key("") == bp.JUDGMENT
    assert bp.metric_key("Promoter holding") is None


def test_rules_sort_by_pillar_then_must_pass():
    pillars = [r.pillar for r in parsed().blueprint.rules]
    assert pillars == ["Business", "Business", "Quality", "Quality", "Quality", "Safety", "Valuation"]
    quality = [r for r in parsed().blueprint.rules if r.pillar == "Quality"]
    assert [r.must for r in quality] == [True, True, False]


def test_fingerprint_ignores_row_order():
    pages = json.loads((FIX / "notion_blueprint.json").read_text())["results"]
    a = bp.parse_rules(pages).blueprint
    b = bp.parse_rules(list(reversed(pages))).blueprint
    assert a.fingerprint() == b.fingerprint()
    assert Blueprint.from_json(a.to_json()).fingerprint() == a.fingerprint()


def test_diff_reports_changed_added_removed_and_pass_bar():
    old = parsed().blueprint
    rules = [r for r in old.rules if r.text != "Moat"]
    rules = [Rule(r.text, r.pillar, r.metric, r.op, 12, r.must) if r.metric == "roe" else r for r in rules]
    rules.append(Rule("Profit CAGR at least 10%", "Growth", "ni_cagr", ">=", 10, False))
    new = Blueprint(rules, 75)
    d = bp.diff(old, new)
    assert [(a.threshold, b.threshold) for a, b in d.changed] == [(15, 12)]
    assert [r.text for r in d.added] == ["Profit CAGR at least 10%"]
    assert [r.text for r in d.removed] == ["Moat"]
    assert d.summary() == "1 changed, 1 added, 1 removed, pass bar 70% → 75%"
    assert not bp.diff(old, Blueprint(list(reversed(old.rules)), old.pass_bar))
    assert old.fingerprint() != new.fingerprint()


def test_condition_text():
    r = Rule("x", "Quality", "roe", ">=", 15, True)
    assert r.condition() == "ROE ≥ 15.0%"
    assert Rule("x", "Quality", "loss_years", "<=", 1, True).condition() == "Consecutive loss years ≤ 1 year"
    assert Rule("x", "Business", bp.JUDGMENT, "", None, True).condition() == "your judgment"


def test_store_keeps_every_locked_version():
    from stockbot.store import Store

    store = Store(":memory:")
    assert store.locked_blueprint() is None
    v1 = Blueprint([Rule("ROE at least 15%", "Quality", "roe", ">=", 15, True)])
    v2 = Blueprint(v1.rules + [Rule("Moat", "Business", bp.JUDGMENT, "", None, True)], 70)
    assert store.lock_blueprint(v1) == 1
    assert store.lock_blueprint(v2) == 2
    latest = store.locked_blueprint()
    assert latest.version == 2 and latest.locked_at > 0
    assert latest.blueprint.fingerprint() == v2.fingerprint() and latest.blueprint.pass_bar == 70
    assert store.blueprint_versions() == [1, 2]


def csv_pages():
    """The starter CSV as Notion imports it: the first column becomes the title, the rest text."""
    import csv

    root = Path(__file__).parents[1]
    with open(root / "templates" / "blueprint.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    def text(v):
        return {"type": "rich_text", "rich_text": [{"plain_text": v}] if v else []}
    return [
        {"properties": {
            "Rule": {"type": "title", "title": [{"plain_text": r["Rule"]}]},
            **{k: text(v) for k, v in r.items() if k != "Rule"},
        }}
        for r in rows
    ]


def test_starter_csv_parses_cleanly():
    p = bp.parse_rules(csv_pages())
    assert p.problems == []
    assert len(p.blueprint.rules) == 15 and p.blueprint.pass_bar == 75
    rules = by_text(p.blueprint.rules)
    loss = rules["Profit-making: not loss-making in each of the last 2 years"]
    assert (loss.metric, loss.op, loss.threshold, loss.must) == ("loss_years", "<=", 1, True)
    yoy = rules["Latest quarter profit up year on year"]
    assert (yoy.metric, yoy.op, yoy.threshold, yoy.must) == ("ni_yoy", ">=", 0, False)
    assert len(p.blueprint.judgment_rules) == 4
    assert sum(r.must for r in p.blueprint.rules) == 7
