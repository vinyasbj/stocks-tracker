import pytest

from stockbot.analysis.blueprint import JUDGMENT, Rule
from stockbot.analysis.thesis import Thesis
from stockbot.analysis.verdict import Check, decide, evaluate, mismatch

ROE = Rule("ROE at least 15%", "Quality", "roe", ">=", 15, True)
DE = Rule("Debt/equity at most 1", "Safety", "debt_to_equity", "<=", 1, True)
CR = Rule("Current ratio at least 1.2", "Safety", "current_ratio", ">=", 1.2, False)
FCF = Rule("FCF positive at least 3 years", "Quality", "fcf_positive_years", ">=", 3, False)
NM = Rule("Net margin at least 8%", "Quality", "net_margin", ">=", 8, False)
REV_YOY = Rule("Revenue up YoY", "Growth", "rev_yoy", ">=", 0, False)
NI_YOY = Rule("Profit up YoY", "Growth", "ni_yoy", ">=", 0, False)
PE = Rule("P/E at most 40", "Valuation", "pe", "<=", 40, False)
LOSS = Rule("Profit-making: not loss-making in each of the last 2 years", "Quality", "loss_years", "<=", 1, True)
MOAT = Rule("Durable moat", "Business", JUDGMENT, "", None, True)

TECH = {"sector": "Technology", "industry": "Software"}
BANK = {"sector": "Financial Services", "industry": "Banks - Regional"}


def one(rule, m, f=TECH, thesis=None):
    return evaluate([rule], m, f, thesis)[0]


# --- each rule: pass / fail / unknown / not applicable ---

def test_numeric_pass_shows_the_value():
    c = one(ROE, {"roe": 46.2})
    assert (c.status, c.note) == ("pass", "46.2%")


def test_no_data_is_unknown():
    c = one(CR, {})
    assert (c.status, c.note) == ("unknown", "no data")


@pytest.mark.parametrize("rule", [DE, CR, FCF])
def test_leverage_rules_do_not_apply_to_banks(rule):
    c = one(rule, {"debt_to_equity": 8, "current_ratio": 0.2, "fcf_positive_years": 0}, BANK)
    assert c.status == "na"


def test_out_of_date_quarter_makes_yoy_rules_unknown():
    m = {"quarter_stale": True, "rev_yoy": -20.0, "ni_yoy": -40.0}
    assert {one(r, m).status for r in (REV_YOY, NI_YOY)} == {"unknown"}
    assert "out of date" in one(REV_YOY, m).note


def test_negative_earnings_fail_a_pe_limit():
    c = one(PE, {"pe": None, "net_margin": -3.0})
    assert (c.status, c.note) == ("fail", "negative earnings")
    assert one(PE, {"pe": -12.0}).status == "fail"
    assert one(PE, {"pe": None}).status == "unknown"  # no P/E and no sign of losses: just no data


def test_loss_making_two_years_in_a_row():
    c = one(LOSS, {"loss_years": 2})
    assert (c.status, c.note) == ("fail", "2 years")
    assert decide([c], held=False, pass_bar=75).code == "AVOID"
    assert decide([c], held=True, pass_bar=75).code == "SELL"


def test_one_loss_year_passes():
    c = one(LOSS, {"loss_years": 1})
    assert (c.status, c.note) == ("pass", "1 year")


def test_no_annual_results_make_the_loss_rule_unknown():
    assert one(LOSS, {"loss_years": None}).status == "unknown"


def test_judgment_rules_follow_the_tick_boxes():
    ticked = Thesis("p", todos=[("Durable moat", True)])
    unticked = Thesis("p", todos=[("Durable moat", False)])
    assert one(MOAT, {}, thesis=ticked).status == "pass"
    assert (one(MOAT, {}, thesis=unticked).status, one(MOAT, {}, thesis=unticked).note) == ("unknown", "not ticked in Notion")
    assert one(MOAT, {}, thesis=Thesis("p")).note == "no tick box for it in Notion"
    assert one(MOAT, {}).note == "no thesis in Notion"
    assert evaluate([MOAT], {}, TECH, None, "thesis could not be read")[0].note == "thesis could not be read"


def test_cagr_note_shows_short_history():
    rule = Rule("Revenue CAGR", "Growth", "rev_cagr", ">=", 10, False)
    assert one(rule, {"rev_cagr": 12.0, "rev_cagr_years": 2}).note == "12.0% over 2y"


# --- verdicts ---

def chk(rule, status, value=None, note=""):
    return Check(rule, status, value, note)


def scored(n_pass, n_fail, n_unknown=0):
    out = []
    for i in range(n_pass + n_fail + n_unknown):
        status = "pass" if i < n_pass else "fail" if i < n_pass + n_fail else "unknown"
        out.append(chk(Rule(f"scored {i}", "Growth", "rev_yoy", ">=", 0, False), status, 1.0, "1.0%"))
    return out


PASS_CORE = [chk(ROE, "pass", 20, "20.0%"), chk(MOAT, "pass")]


@pytest.mark.parametrize(
    "checks,held,code,reason",
    [
        # not held
        ([chk(DE, "fail", 1.8, "1.80"), *PASS_CORE], False, "AVOID", "Fails a must-pass rule: Debt/Equity 1.80 (need ≤ 1.00)"),
        ([*PASS_CORE, *scored(1, 3)], False, "AVOID", "passes only 1 of 4 scored rules (needs 75%)"),
        ([chk(ROE, "pass", 20, "20.0%"), chk(MOAT, "unknown", note="not ticked in Notion")], False, "WATCH", "confirm: Durable moat"),
        ([*PASS_CORE, chk(PE, "fail", 52, "52.0")], False, "WATCH", "the price fails: P/E 52.0 (need ≤ 40.0)"),
        ([*PASS_CORE, chk(PE, "unknown", note="no data")], False, "WATCH", "valuation can't be checked"),
        ([*PASS_CORE, *scored(3, 1), chk(PE, "pass", 20, "20.0")], False, "BUY", "Passes every rule"),
        # held
        ([chk(ROE, "fail", 13.8, "13.8%"), chk(MOAT, "pass")], True, "SELL", "Breaks a must-pass rule: ROE 13.8% (need ≥ 15.0%)"),
        ([*PASS_CORE, *scored(3, 2)], True, "REVIEW", "passes only 3 of 5 scored rules (needs 75%)"),
        ([*PASS_CORE, chk(PE, "fail", 52, "52.0")], True, "HOLD", "too expensive to add"),
        ([chk(ROE, "pass", 20, "20.0%"), chk(MOAT, "unknown", note="no thesis in Notion")], True, "HOLD", "confirm: Durable moat"),
        ([*PASS_CORE, chk(PE, "pass", 20, "20.0")], True, "ADD", "Passes every rule"),
    ],
)
def test_verdicts(checks, held, code, reason):
    v = decide(checks, held, 75)
    assert v.code == code
    assert reason in v.reason


def test_unknowns_and_not_applicable_never_cause_sell_or_review():
    checks = [chk(ROE, "unknown"), chk(DE, "na"), chk(MOAT, "unknown"), *scored(1, 0, 5)]
    assert decide(checks, True, 75).code == "HOLD"


def test_scored_share_ignores_unknowns():
    # 3 pass, 1 fail, 2 unknown: 75% meets a 75% bar.
    assert decide([*PASS_CORE, *scored(3, 1, 2)], True, 75).code == "ADD"
    assert decide([*PASS_CORE, *scored(3, 1, 2)], True, 80).code == "REVIEW"


def test_no_known_scored_rules_skips_the_bar():
    assert decide([*PASS_CORE, *scored(0, 0, 3)], False, 75).code == "BUY"


def test_counts_exclude_not_applicable():
    v = decide([chk(ROE, "pass"), chk(DE, "na"), chk(MOAT, "unknown")], True, 75)
    assert (v.passed, v.counted) == (1, 2)


def test_reason_names_at_most_three_rules():
    fails = [chk(Rule(f"m{i}", "Quality", "roe", ">=", 15, True), "fail", 1, "1.0%") for i in range(5)]
    assert decide(fails, False, 75).reason.endswith("+2 more")


@pytest.mark.parametrize(
    "conviction,code,flagged",
    [("HIGH", "SELL", True), ("HIGH", "REVIEW", True), ("HIGH", "AVOID", True), ("HIGH", "HOLD", False),
     ("LOW", "ADD", True), ("LOW", "BUY", True), ("LOW", "SELL", False), ("MEDIUM", "SELL", False), (None, "SELL", False)],
)
def test_conviction_mismatch(conviction, code, flagged):
    from stockbot.analysis.verdict import Verdict

    assert bool(mismatch(conviction, Verdict(code, "", [], code in ("ADD", "HOLD", "REVIEW", "SELL")))) is flagged
