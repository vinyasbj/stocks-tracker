from datetime import date

from stockbot.analysis import fmt, metrics, signals


def q(period, revenue, net_income, operating_income=None):
    return {"period": period, "revenue": revenue, "net_income": net_income, "operating_income": operating_income, "eps": None}


FUND = {
    "sector": "Technology",
    "industry": "Software",
    "currency": "USD",
    "price": 90.0,
    "prev_close": 100.0,
    "high_52w": 150.0,
    "low_52w": 60.0,
    "pe": 70.0,
    "roe": 0.20,
    "net_margin": 0.18,
    "current_ratio": 0.8,
    "debt_to_equity_pct": 200.0,
    "quarterly": [
        q("2026-09-30", 120, 30, 30),
        q("2026-06-30", 110, 25, 25),
        q("2026-03-31", 105, 24, 24),
        # Yahoo skips quarters; the year-ago quarter must be found by date, not position.
        q("2025-09-30", 100, 20, 20),
    ],
    "annual": [
        {"period": "2026-06-30", "revenue": 400, "net_income": 90, "fcf": 50, "total_debt": 300, "equity": 100},
        {"period": "2025-06-30", "revenue": 350, "net_income": 80, "fcf": 40},
        {"period": "2024-06-30", "revenue": 300, "net_income": 70, "fcf": 30},
        {"period": "2023-06-30", "revenue": 250, "net_income": 60, "fcf": 20},
    ],
}
TODAY = date(2026, 11, 4)


def test_growth_matches_year_ago_quarter_by_date():
    m = metrics.compute(FUND, TODAY)
    assert m["yoy_quarter"] == "2025-09-30"
    assert round(m["rev_yoy"], 1) == 20.0
    assert round(m["ni_yoy"], 1) == 50.0
    assert round(m["rev_qoq"], 2) == 9.09
    assert round(m["op_margin_change_yoy"], 1) == 5.0
    assert round(m["rev_cagr"], 1) == 17.0
    assert m["debt_to_equity"] == 3.0  # from the balance sheet, not Yahoo's percentage
    assert m["fcf_positive_years"] == 4
    assert round(m["from_52w_high"]) == -40


def test_profit_cagr_and_operating_margin():
    m = metrics.compute(FUND, TODAY)
    assert round(m["ni_cagr"], 1) == 14.5  # 60 -> 90 over 3 years
    assert m["ni_cagr_years"] == 3
    assert m["op_margin"] == 25.0  # no TTM figure: the latest quarter's (30 / 120)
    assert metrics.compute({**FUND, "operating_margin": 0.31}, TODAY)["op_margin"] == 31.0


def test_loss_streak():
    def years(*incomes):
        return {**FUND, "annual": [{"period": f"{2026 - i}-06-30", "revenue": 100, "net_income": x} for i, x in enumerate(incomes)]}

    assert metrics.compute(years(-5, -3, 10), TODAY)["loss_years"] == 2
    assert metrics.compute(years(-5, 3, -10), TODAY)["loss_years"] == 1
    assert metrics.compute(years(5, -3, -10), TODAY)["loss_years"] == 0
    assert metrics.compute({**FUND, "annual": []}, TODAY)["loss_years"] is None


def test_yahoo_growth_fills_a_missing_year_ago_quarter():
    f = {**FUND, "quarterly": FUND["quarterly"][:3], "revenue_growth_yoy": 0.298, "earnings_growth_yoy": 0.407}
    m = metrics.compute(f, TODAY)
    assert round(m["rev_yoy"], 1) == 29.8
    assert round(m["ni_yoy"], 1) == 40.7


def test_stale_quarters_are_flagged_and_ignored_by_rules():
    f = {**FUND, "quarterly": [q("2025-06-30", 80, 10), q("2025-03-31", 100, 20)], "revenue_growth_yoy": 0.5}
    m = metrics.compute(f, TODAY)
    assert m["quarter_stale"] is True
    assert m["rev_yoy"] is None  # Yahoo's growth figure may describe a newer quarter
    texts = [s.text for s in signals.evaluate({**m, "rev_yoy": -20.0}, f)]
    assert not any("year on year in the latest quarter" in t for t in texts)


def test_negative_base_growth_reads_as_improvement():
    assert metrics.pct_change(-5, -10) == 50.0
    assert metrics.pct_change(10, 0) is None


def test_signals():
    m = metrics.compute(FUND, TODAY)
    s = signals.evaluate(m, FUND)
    strengths = {x.text for x in s if x.kind == "strength"}
    weaknesses = {x.text for x in s if x.kind == "weakness"}
    assert "Revenue up 20.0% year on year in the latest quarter" in strengths
    assert "High return on equity: 20.0%" in strengths
    assert "Positive free cash flow in each of the last 4 years" in strengths
    assert "High debt: debt/equity 3.00" in weaknesses
    assert "Expensive: P/E 70.0" in weaknesses
    assert "Trading 40% below its 52-week high" in weaknesses
    assert any(t.startswith("Tight liquidity") for t in weaknesses)


def test_banks_skip_leverage_and_liquidity_rules():
    bank = {**FUND, "sector": "Financial Services", "industry": "Banks - Regional"}
    m = metrics.compute(bank, TODAY)
    texts = " ".join(s.text for s in signals.evaluate(m, bank))
    assert "debt/equity" not in texts and "current ratio" not in texts
    assert "debt_to_equity" not in signals.bands(m, bank)


def test_bands():
    b = signals.bands({"roe": 8.8, "net_margin": 6.8, "pe": 21.6, "debt_to_equity": 0.44, "current_ratio": 1.1}, FUND)
    assert b == {"roe": "low", "net_margin": "average", "pe": "moderate", "debt_to_equity": "moderate", "current_ratio": "adequate"}


def test_money_formats():
    assert fmt.money(3306010000000.0, "INR") == "₹3,30,601 Cr"
    assert fmt.money(109417000000.0, "USD") == "$109.42B"
    assert fmt.money(None, "USD") == "n/a"
    assert fmt.indian_group(1234567) == "12,34,567"
    assert fmt.pct(-1.234) == "-1.2%"


def test_only_the_conviction_prompt_may_speak_of_buying_or_selling():
    from stockbot.llm import prompts

    systems = {name: v for name, v in vars(prompts).items() if name.endswith("_SYSTEM")}
    advice_free = {"SUMMARY_SYSTEM", "RESULTS_SYSTEM", "QA_SYSTEM"}
    for name in advice_free:
        assert prompts.NO_ADVICE in systems[name], name
    assert prompts.NO_ADVICE not in systems["CONVICTION_SYSTEM"]
    assert "never change" in systems["CONVICTION_SYSTEM"]
    assert "no numbers" in systems["PROFILE_SYSTEM"]
    assert "check" in prompts.Intent.model_fields["intent"].annotation.__args__
