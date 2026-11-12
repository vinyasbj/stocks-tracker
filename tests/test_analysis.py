from datetime import date

import pytest

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


def test_graham_number_computation():
    # 1. Explicit EPS and BVPS
    f = {"price": 100.0, "eps": 5.0, "bvps": 20.0, "pe": 20.0, "pb": 5.0}
    m = metrics.compute(f)
    # Graham number = sqrt(22.5 * 5.0 * 20.0) = sqrt(2250) = 47.434...
    assert round(m["graham_number"], 2) == 47.43
    # Price to Graham = 100 / 47.434 = 2.11
    assert round(m["price_to_graham"], 2) == 2.11

    # 2. Derived from P/E and P/B when explicit eps/bvps missing
    f2 = {"price": 50.0, "pe": 10.0, "pb": 1.5}
    m2 = metrics.compute(f2)
    # Ratio = sqrt((10 * 1.5) / 22.5) = sqrt(15 / 22.5) = sqrt(0.6666...) = 0.816
    assert round(m2["price_to_graham"], 2) == 0.82
    assert round(m2["graham_number"], 2) == 61.24


def test_graham_intrinsic_value_computation():
    # US stock (Bond yield defaults to 4.4%)
    f_us = {"price": 100.0, "eps": 4.0, "pe": 25.0, "currency": "USD"}
    # Growth = 10% -> IV = (4.0 * (8.5 + 2 * 10) * 4.4) / 4.4 = 4.0 * 28.5 = 114.0
    m_us = metrics.compute({**f_us, "quarterly": [], "annual": [
        {"period": "2026-06-30", "revenue": 100, "net_income": 121},
        {"period": "2025-06-30", "revenue": 100, "net_income": 110},
        {"period": "2024-06-30", "revenue": 100, "net_income": 100},
    ]})
    assert round(m_us["graham_iv"], 1) == 114.0
    assert round(m_us["price_to_graham_iv"], 2) == round(100.0 / 114.0, 2)

    # Indian stock (Bond yield 7.0%)
    f_in = {"price": 500.0, "eps": 25.0, "pe": 20.0, "currency": "INR", "annual": [
        {"period": "2026-03-31", "revenue": 100, "net_income": 110},
        {"period": "2025-03-31", "revenue": 100, "net_income": 105},
        {"period": "2024-03-31", "revenue": 100, "net_income": 100},
    ]}
    m_in = metrics.compute(f_in)
    # CAGR 100 -> 110 over 2 years = sqrt(1.1) - 1 = 4.88%
    # IV = (25.0 * (8.5 + 2 * 4.88) * 4.4) / 7.0 = (25.0 * 18.26 * 4.4) / 7.0 = 287.0
    assert round(m_in["graham_iv"], 1) == 287.0


def test_is_asset_light():
    assert signals.is_asset_light({"sector": "Technology", "industry": "Software"}) is True
    assert signals.is_asset_light({"sector": "Communication Services", "industry": "Internet Content"}) is True
    assert signals.is_asset_light({"sector": "Industrials", "industry": "Freight"}) is False
    assert signals.is_asset_light({"sector": "Utilities", "industry": "Electric"}) is False
    assert signals.is_asset_light({"sector": "Basic Materials", "industry": "Steel"}) is False
    assert signals.is_asset_light({}) is False


def test_peg_cash_conversion_and_promoter_holding():
    annual = [
        {"period": "2026-03-31", "revenue": 100, "net_income": 133.1, "operating_cf": 100.0},
        {"period": "2025-03-31", "revenue": 100, "net_income": 121},
        {"period": "2024-03-31", "revenue": 100, "net_income": 110},
        {"period": "2023-03-31", "revenue": 100, "net_income": 100},
    ]
    m = metrics.compute({"price": 100.0, "pe": 20.0, "insiders_held": 0.625, "annual": annual})
    assert round(m["cash_conversion"], 1) == 75.1  # 100 / 133.1
    assert round(m["peg"], 2) == 2.0  # 20 / 10% profit CAGR
    assert m["promoter_holding"] == 62.5
    # Yahoo's own PEG wins over the derived one
    assert metrics.compute({"pe": 20.0, "peg": 0.9, "annual": annual})["peg"] == 0.9
    # a loss gives no cash conversion, shrinking profit no PEG
    loss = [{"period": "2026-03-31", "revenue": 100, "net_income": -5, "operating_cf": 10.0}]
    m2 = metrics.compute({"pe": 20.0, "annual": loss})
    assert "cash_conversion" not in m2 and m2["peg"] is None and "promoter_holding" not in m2


# --- P/E against its own past, cap-adjusted CAGR, peers ---

YEARS = [  # newest first: (period, EPS, price at year end) -> P/E 20, 25, 30, 22
    {"period": "2026-03-31", "eps": 10.0, "price_end": 200.0},
    {"period": "2025-03-31", "eps": 8.0, "price_end": 200.0},
    {"period": "2024-03-31", "eps": 6.0, "price_end": 180.0},
    {"period": "2023-03-31", "eps": 5.0, "price_end": 110.0},
]


def test_pe_against_its_median_of_past_year_ends():
    m = metrics.compute({"price": 230.0, "pe": 23.0, "annual": YEARS}, today=date(2026, 11, 12))
    assert m["pe_median"] == 23.5  # median of 20, 25, 30, 22
    assert m["pe_median_years"] == 4
    assert round(m["pe_vs_median"], 3) == round(23 / 23.5, 3)


def test_pe_median_needs_three_year_ends():
    m = metrics.compute({"price": 230.0, "pe": 23.0, "annual": YEARS[:2]}, today=date(2026, 11, 12))
    assert "pe_vs_median" not in m


def test_pe_contraction_against_the_year_end_about_a_year_back():
    # 2026-03-31 is 226 days back (nearest to 365 in the 6-18 month window): P/E then 20, price then 200.
    up_cheaper = metrics.compute({"price": 230.0, "pe": 18.0, "annual": YEARS}, today=date(2026, 11, 12))
    assert up_cheaper["pe_then_date"] == "2026-03-31" and up_cheaper["pe_then"] == 20.0
    assert up_cheaper["pe_contraction"] == 1.0 and round(up_cheaper["price_change_since"], 1) == 15.0
    up_dearer = metrics.compute({"price": 230.0, "pe": 23.0, "annual": YEARS}, today=date(2026, 11, 12))
    assert up_dearer["pe_contraction"] == 0.0
    down_cheaper = metrics.compute({"price": 150.0, "pe": 15.0, "annual": YEARS}, today=date(2026, 11, 12))
    assert down_cheaper["pe_contraction"] == 0.0  # P/E fell only because the price did


def test_pe_contraction_is_no_when_there_is_no_profit_now():
    m = metrics.compute({"price": 230.0, "pe": None, "eps": -2.0, "annual": YEARS}, today=date(2026, 11, 12))
    assert m["pe_contraction"] == 0.0


def test_past_pes_skip_a_currency_mismatch():
    f = {"price": 1500.0, "pe": 23.0, "currency": "INR", "financial_currency": "USD", "annual": YEARS}
    m = metrics.compute(f, today=date(2026, 11, 12))
    assert "pe_vs_median" not in m and "pe_contraction" not in m


@pytest.mark.parametrize(
    "cap,currency,band",
    [(3e12, "USD", "large"), (5e9, "USD", "mid"), (1e9, "USD", "small"),
     (9e11, "INR", "large"), (3e11, "INR", "mid"), (1e11, "INR", "small"), (5e9, "EUR", None)],
)
def test_cap_bands(cap, currency, band):
    assert metrics.cap_band(cap, currency) == band


def test_cap_adjusted_profit_cagr_is_the_margin_over_the_bands_bar():
    annual = [{"period": f"{2026 - i}-03-31", "net_income": 100 / 1.12**i} for i in range(4)]  # 12% CAGR
    m = metrics.compute({"market_cap": 5e9, "currency": "USD", "annual": annual})
    assert m["cap_band"] == "mid" and m["cap_hurdle"] == 15.0
    assert round(m["cap_profit_cagr"], 1) == -3.0


def test_peer_outperformance():
    peers = {"INFY.NS": 0.05, "HCLTECH.NS": 0.08, "WIPRO.NS": 0.10, "TECHM.NS": 0.20, "TCS.NS": 0.99}
    m = {"eps": 140.0}
    metrics.peer_outperformance(m, {"earnings_growth_yoy": 0.15}, peers, "TCS", "NSE")
    assert m["peer_count"] == 4 and round(m["peer_eps_growth_median"], 1) == 9.0  # TCS.NS itself left out
    assert m["eps_vs_peers"] == 1.0 and m["peer_market"] == "NSE"
    adr = {"eps": 1.0}
    metrics.peer_outperformance(adr, {"earnings_growth_yoy": 0.15}, peers, "INFY", "NSE")  # the ADR drops INFY.NS
    assert adr["peer_count"] == 4
    loss = {"eps": -1.0}
    metrics.peer_outperformance(loss, {"earnings_growth_yoy": 0.50}, peers, "TCS", "NSE")
    assert loss["eps_vs_peers"] == 0.0  # EPS must be positive
    few = {"eps": 1.0}
    metrics.peer_outperformance(few, {"earnings_growth_yoy": 0.15}, {"IBM": 0.05, "ACN": 0.08}, "MSFT", "US")
    assert "eps_vs_peers" not in few


def test_at_most_ten_peers_count():
    peers = {f"P{i}": i / 100 for i in range(11)}
    m = {"eps": 1.0}
    metrics.peer_outperformance(m, {"earnings_growth_yoy": 0.5}, peers, "XYZ", "US")
    assert m["peer_count"] == 10


def test_missing_history_and_peers_say_why():
    f = {"price": 1500.0, "pe": 23.0, "currency": "INR", "financial_currency": "USD", "annual": YEARS}
    m = metrics.compute(f, today=date(2026, 11, 12))
    assert m["pe_vs_median_why"] == "priced in INR but reports in USD"
    short = metrics.compute({"price": 230.0, "pe": 23.0, "annual": YEARS[:2]}, today=date(2026, 11, 12))
    assert short["pe_vs_median_why"] == "only 2 profitable year ends with a price"
    few = {"eps": 1.0}
    metrics.peer_outperformance(few, {"earnings_growth_yoy": 0.15}, {"IBM": 0.05}, "MSFT", "US")
    assert few["eps_vs_peers_why"] == "only 1 US industry peer with EPS growth on Yahoo"
