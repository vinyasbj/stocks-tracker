# Spec Delta

## Purpose

Judging one stock against the locked conviction blueprint: each rule is checked against the data and the stock's thesis, and code turns the results into a verdict. The local model explains the verdict but never decides it.

## ADDED Requirements

### Requirement: Each rule is checked to pass, fail, unknown or not applicable
For each rule in the locked blueprint the bot SHALL produce one of: pass, fail, unknown (no data, out-of-date data, or an unconfirmed judgment rule), or not applicable. A numeric result SHALL show the stock's value.

#### Scenario: Numeric pass
- **WHEN** the rule is ROE ≥ 15 and the stock's ROE is 46.2%
- **THEN** the rule passes and shows 46.2%

#### Scenario: No data
- **WHEN** Yahoo has no current ratio for the stock
- **THEN** the current-ratio rule is unknown, with "no data"

#### Scenario: Leverage rules on a bank
- **WHEN** the stock is a bank or other financial company and the rule uses Debt/Equity, Current ratio or FCF positive years
- **THEN** the rule is not applicable and does not count towards any total

#### Scenario: Out-of-date quarter
- **WHEN** Yahoo's latest quarter for the stock is out of date
- **THEN** rules on Revenue growth YoY and Profit growth YoY are unknown, with that reason

#### Scenario: Negative earnings
- **WHEN** a rule limits P/E and the company has negative earnings
- **THEN** the rule fails, with "negative earnings"

#### Scenario: Loss-making two years in a row
- **WHEN** the must-pass rule is Consecutive loss years ≤ 1 and the company lost money in each of its last two fiscal years
- **THEN** the rule fails, showing "2 years", so the verdict is AVOID for a stock not in the portfolio and SELL for a holding

#### Scenario: One loss year
- **WHEN** the same rule applies and only the latest fiscal year was a loss
- **THEN** the rule passes, showing "1 year"

#### Scenario: No annual results
- **WHEN** Yahoo has no annual results for the company
- **THEN** the Consecutive loss years rule is unknown

#### Scenario: Judgment rule
- **WHEN** a judgment rule is ticked on the stock's page
- **THEN** it passes; otherwise it is unknown

### Requirement: The scored-rule share is computed from known results
Scored rules are rules that are neither must-pass nor in the Valuation pillar. The scored share SHALL be passed ÷ (passed + failed) over scored rules. Unknown and not-applicable results are excluded. When no scored rule has a known result, the pass bar SHALL NOT be applied.

#### Scenario: Share below the bar
- **WHEN** the bar is 75% and a stock passes 5 of 8 known scored rules (62.5%)
- **THEN** the stock is below the bar

#### Scenario: Unknowns excluded
- **WHEN** a stock passes 3 scored rules, fails 1, and 2 are unknown
- **THEN** its share is 75%, which meets a 75% bar

### Requirement: A stock that is not held gets BUY, WATCH or AVOID
A stock not in the Notion portfolio SHALL get the first verdict that applies, in this order: AVOID if a must-pass rule outside Valuation fails; AVOID if the scored share is below the bar; WATCH if a must-pass rule outside Valuation is unknown; WATCH if a Valuation rule fails or is unknown; otherwise BUY. The reason SHALL name up to three of the rules that decided it.

#### Scenario: Must-pass rule fails
- **WHEN** the must-pass rule Debt/Equity ≤ 1 fails with 1.8
- **THEN** the verdict is AVOID, with reason "Fails a must-pass rule: Debt/Equity 1.80 (need ≤ 1.00)"

#### Scenario: Judgment not confirmed
- **WHEN** every numeric rule passes but the must-pass judgment rule "Durable moat" is not ticked
- **THEN** the verdict is WATCH, with reason naming "Durable moat" as needing confirmation

#### Scenario: Good business, high price
- **WHEN** every non-valuation rule passes or meets the bar and the Valuation rule P/E ≤ 40 fails with 52
- **THEN** the verdict is WATCH, not AVOID

#### Scenario: Everything passes
- **WHEN** all must-pass rules pass, the scored share meets the bar, and every Valuation rule passes
- **THEN** the verdict is BUY

### Requirement: A held stock gets ADD, HOLD, REVIEW or SELL
A stock in the Notion portfolio SHALL get the first verdict that applies, in this order: SELL if a must-pass rule outside Valuation fails; REVIEW if the scored share is below the bar; HOLD if a Valuation rule fails; HOLD if a must-pass rule or Valuation rule is unknown; otherwise ADD. An unknown result SHALL NOT by itself cause SELL or REVIEW.

#### Scenario: Must-pass rule breaks
- **WHEN** a held stock's ROE falls to 13.8% under the must-pass rule ROE ≥ 15
- **THEN** the verdict is SELL, with that rule as the reason

#### Scenario: Weakening
- **WHEN** a held stock passes every must-pass rule but only 60% of known scored rules, with a 75% bar
- **THEN** the verdict is REVIEW

#### Scenario: Too expensive to add
- **WHEN** a held stock passes everything except the Valuation rule P/E ≤ 40
- **THEN** the verdict is HOLD, with reason "too expensive to add"

#### Scenario: Thesis not yet written
- **WHEN** a held stock passes every numeric rule but none of its must-pass judgment rules is ticked
- **THEN** the verdict is HOLD, naming the rules to confirm, and not SELL or REVIEW

#### Scenario: Everything passes
- **WHEN** a held stock passes every rule, Valuation included
- **THEN** the verdict is ADD

### Requirement: `/check` shows the verdict, the rules, the thesis and an explanation
`/check TICKER` SHALL reply with, in order: the stock header; the verdict and its reason; the business profile; the blueprint version and how many counted rules pass; every rule by pillar with its result, value and must-pass flag; the thesis (conviction, one-line thesis, pillars, breakers, action plan) or a note that there is none; the model's summary; and a disclaimer saying the verdict applies the investor's own rules.

#### Scenario: Check a holding with a thesis
- **WHEN** the investor sends `/check GOOGL` for a holding with a thesis
- **THEN** the reply has a held-stock verdict, the rule list, the thesis sections, and a summary

#### Scenario: Ambiguous ticker
- **WHEN** the query matches more than one listed stock
- **THEN** the bot shows buttons to pick one, as other commands do, and checks the chosen stock

### Requirement: `/check` shows how and where the company makes money
The business profile SHALL have two parts. First, at most three lines from the local model, based only on Yahoo's company description: business lines or segments, main customers, and regions served. Second, company-level profit figures computed by code: latest-quarter net profit with YoY and QoQ growth, latest full-year net profit, profit CAGR 3Y, and net and operating margin. The model's lines SHALL contain no numbers.

#### Scenario: Profitable company
- **WHEN** the investor checks GOOGL
- **THEN** the profile names its segments (such as Google Services, Google Cloud, Other Bets) and regions, followed by its latest-quarter net profit, profit growth YoY and QoQ, full-year profit, profit CAGR 3Y and margins

#### Scenario: Loss-making company
- **WHEN** the company's latest quarter is a net loss
- **THEN** the profit part says "Loss-making" with the net loss, and the description is still shown

#### Scenario: No segment split available
- **WHEN** a profile is shown for any stock
- **THEN** it states that a segment or country split of profit is not available from Yahoo, and no such split is shown or estimated

#### Scenario: Model adds a number
- **WHEN** the model's profile text contains a number that is not in Yahoo's description
- **THEN** the bot shows the start of Yahoo's description instead of the model's text

#### Scenario: No description
- **WHEN** Yahoo has no description for the stock
- **THEN** the profile shows only the profit figures, with a note that no description is available

#### Scenario: Model unavailable
- **WHEN** the local model cannot be reached
- **THEN** the profile shows the start of Yahoo's description and the profit figures

### Requirement: The model explains the verdict but never decides it
The verdict and every rule result SHALL be computed without the model. The model's explanation SHALL be shown separately as a summary. It is given the data, the rule results, the verdict and the thesis, and is instructed to restate the verdict without changing it and to say whether recent data supports or challenges the thesis. When the model is unavailable, the bot SHALL still show the verdict and rule results, with a notice in place of the summary.

#### Scenario: Ollama not running
- **WHEN** the local model cannot be reached
- **THEN** `/check` shows the verdict and every rule result, and the summary says the local model is unavailable

#### Scenario: Verdict shown before the explanation
- **WHEN** the model takes 20 seconds to write the explanation
- **THEN** the verdict and rules are already visible, and the message is updated when the summary is ready

### Requirement: Conviction mismatches are flagged
When the thesis gives a conviction level, the bot SHALL show it beside the verdict. It SHALL flag a mismatch when the conviction is HIGH and the verdict is SELL, REVIEW or AVOID, or when the conviction is LOW and the verdict is ADD or BUY.

#### Scenario: High conviction, broken rule
- **WHEN** GOOGL's thesis says Conviction HIGH and its verdict is SELL
- **THEN** the reply flags that the investor's conviction and the blueprint disagree

### Requirement: `/why`, free text and the CLI reach the same check
`/why TICKER` SHALL behave exactly like `/check TICKER`. A free-text message asking whether to buy, sell, hold or add a stock, whether it fits the investor's rules, or why the investor owns it SHALL be answered with the check. The CLI SHALL offer `check` and `why` with the same output as plain text.

#### Scenario: Free-text question
- **WHEN** the investor writes "why do I own Google?"
- **THEN** the bot replies with the check for Alphabet

#### Scenario: CLI
- **WHEN** the investor runs the CLI `check TCS`
- **THEN** it prints the same verdict, rules, thesis and summary as plain text
