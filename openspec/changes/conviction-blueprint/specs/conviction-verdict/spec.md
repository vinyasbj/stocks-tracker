# Spec Delta

## Purpose

Judging one stock against the locked conviction blueprint: each rule is checked against the data and the stock's thesis, and code turns the results into a verdict. The local model explains the verdict but never decides it.

## ADDED Requirements

### Requirement: Each rule is checked to pass, fail, unknown or not applicable
For each rule in the locked blueprint the bot SHALL produce one of: pass, fail, unknown (no data, out-of-date data, or an unconfirmed judgment rule), or not applicable. For a stock that is not held, judgment rules SHALL be not applicable: there is no thesis to tick, so they count neither for nor against it and are left for the investor to confirm before buying. A numeric result SHALL show the stock's value.

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
A stock not in the Notion portfolio SHALL get the first verdict that applies, in this order: AVOID if a must-pass rule outside Valuation fails; AVOID if the scored share is below the bar; WATCH if a must-pass rule outside Valuation is unknown; WATCH if a Valuation rule fails or is unknown; otherwise BUY, and when judgment rules were left to the investor the BUY reason SHALL say how many to confirm before buying. The reason SHALL name up to three of the rules that decided it; two or more judgment rules are counted ("4 judgment rules") rather than named.

#### Scenario: Must-pass rule fails
- **WHEN** the must-pass rule Debt/Equity ≤ 1 fails with 1.8
- **THEN** the verdict is AVOID, with reason "Fails a must-pass rule: Debt/Equity 1.80 (need ≤ 1.00)"

#### Scenario: Judgment rules on a stock not held
- **WHEN** every numeric rule passes, Valuation included, and the blueprint has 4 must-pass judgment rules
- **THEN** the verdict is BUY, with reason "Numbers pass every rule, valuation included; before buying, confirm 4 judgment rules", and `/why` shows the 4 judgment rules as left for the investor to confirm

#### Scenario: Must-pass number missing
- **WHEN** every rule that has data passes but the must-pass rule ROE has no data
- **THEN** the verdict is WATCH, with reason naming ROE as needing confirmation

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

### Requirement: `/why` shows the verdict, the rules, the thesis and an explanation
`/why TICKER` SHALL reply with one compact message, in this order:
1. Stock header with company name, symbol, market, price, day change, P/E, and market cap.
2. Verdict block: verdict icon and code with the locked blueprint version, the reason, the investor's conviction level (or "not set" for a holding without one), any mismatch warning, and any unlocked-edit notice.
3. Rules section (`📏 Rules · N of M pass`). Rules that fail come first, one per line with pillar, value and need, must-pass rules first. Rules that cannot be checked follow, one per line. When none of the judgment rules is confirmed, for the same reason (no thesis, or nothing ticked yet), they are one line with their count and why (for example "4 judgment rules unconfirmed: not ticked in Notion"); once some are ticked, the others are listed by name. For a stock not held they are one line saying they are the investor's to confirm before buying. Not-applicable rules are one line per reason. The rules that pass are folded into one line per pillar with their values; ticked judgment rules are counted, not spelled out.
4. Business & profit section (`🏢 Business & profit`): the business description, then the profit figures.
5. Thesis section (`📝 Your thesis`) with the one-line thesis, pillars, breakers and action plan (at most three lines each), or a one-line placeholder if there is none.
6. The local model's summary, at most three sentences, prefixed `🧠`.
7. Disclaimer naming the blueprint version and stating that verdicts apply the investor's own rules.

The message SHALL stay within Telegram's limit without losing the verdict, the summary or the disclaimer: when it would be too long, the thesis shrinks to its one-line thesis with a pointer to Notion, and if it is still too long the middle is cut on a line boundary.

#### Scenario: Check a holding with a thesis
- **WHEN** the investor sends `/why GOOGL` for a holding with a thesis
- **THEN** the reply has a held-stock verdict, the structured blueprint checklist, the business and profit breakdown, the thesis sections, and a summary

#### Scenario: Ambiguous ticker
- **WHEN** the query matches more than one listed stock
- **THEN** the bot shows buttons to pick one, as other commands do, and checks the chosen stock

### Requirement: `/why` shows how and where the company makes money
The business profile SHALL have two parts. First, at most three lines from the local model, based only on Yahoo's company description: business lines or segments, main customers, and regions served. Second, company-level profit figures computed by code: latest-quarter net profit with YoY and QoQ growth, latest full-year net profit, profit CAGR 3Y, and net and operating margin. The model's lines SHALL contain no numbers.

#### Scenario: Profitable company
- **WHEN** the investor checks GOOGL
- **THEN** the profile names its segments (such as Google Services, Google Cloud, Other Bets) and regions, followed by its latest-quarter net profit, profit growth YoY and QoQ, full-year profit, profit CAGR 3Y and margins

#### Scenario: Loss-making company
- **WHEN** the company's latest quarter is a net loss
- **THEN** the profit part says "Loss-making" with the net loss, and the description is still shown

#### Scenario: A loss that changed
- **WHEN** the latest quarter's loss is larger than the year-ago quarter's loss, or the company moved between profit and loss
- **THEN** the change is said in words ("loss widened 97%", "loss narrowed 50%", "swung to a loss", "back to profit") rather than as a negative growth figure

#### Scenario: No revenue to measure a margin against
- **WHEN** Yahoo reports a net margin of 0 for a company that has a net profit or loss but (almost) no revenue
- **THEN** the net margin is shown as n/a, and a net margin rule is unknown rather than failed on a made-up 0%

#### Scenario: No segment split available
- **WHEN** a profile is shown for any stock
- **THEN** no split of profit by segment or country is shown or estimated (Yahoo has none; the README says so once, rather than every reply)

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
The verdict and every rule result SHALL be computed without the model. The model's explanation SHALL be shown separately as a summary. It is given the data, the rule results, the verdict and the thesis, and is instructed to restate the verdict without changing it and to say whether recent data supports or challenges the verdict, in at most three sentences, without listing the rules that pass and without referring to a thesis when there is none. When the model is unavailable, the bot SHALL still show the verdict and rule results, with a notice in place of the summary.

#### Scenario: Ollama not running
- **WHEN** the local model cannot be reached
- **THEN** `/why` shows the verdict and every rule result, and the summary says the local model is unavailable

#### Scenario: Verdict shown before the explanation
- **WHEN** the model takes 20 seconds to write the explanation
- **THEN** the verdict and rules are already visible, and the message is updated when the summary is ready

### Requirement: ETFs and funds are shown, not judged
`/why` SHALL find an ETF or fund by its exact ticker, even though the NSE and SEC lists the bot searches don't include funds. When the typed ticker is not exactly a listed symbol, an ETF or fund with exactly that ticker SHALL be preferred over company-name matches; a ticker that is not a fund SHALL never replace them. A fund SHALL get no verdict, since the blueprint's rules are for companies. Instead `/why` SHALL show its price, whether it is held (with the conviction level), the fund's figures from Yahoo (family and category, assets, expense ratio, yield, YTD and 3- and 5-year average returns, the 52-week range), the start of Yahoo's description of what it holds, and the investor's thesis. The local model SHALL NOT be called for a fund.

#### Scenario: ETF ticker
- **WHEN** the investor sends `/why QQQM`
- **THEN** the reply is "NOT JUDGED · ETF" with QQQM's figures, its description and the thesis, and no verdict or rule results

#### Scenario: Ticker that also fuzzy-matches company names
- **WHEN** the investor sends `/why URA` and the exchange lists only offer companies whose names resemble "URA"
- **THEN** the bot shows the Global X Uranium ETF, not buttons for those companies

#### Scenario: A word that is not a fund
- **WHEN** the investor sends `/why infosys`
- **THEN** the bot offers Infosys as before

### Requirement: Conviction mismatches are flagged
When the thesis gives a conviction level, the bot SHALL show it beside the verdict. It SHALL flag a mismatch when the conviction is HIGH and the verdict is SELL, REVIEW or AVOID, or when the conviction is LOW and the verdict is ADD or BUY.

#### Scenario: High conviction, broken rule
- **WHEN** GOOGL's thesis says Conviction HIGH and its verdict is SELL
- **THEN** the reply flags that the investor's conviction and the blueprint disagree

### Requirement: Free text and the CLI reach the same check as `/why`
`/why TICKER` SHALL be the only command for the check; there is no `/check` command. A free-text message asking whether to buy, sell, hold or add a stock, whether it fits the investor's rules, or why the investor owns it SHALL be answered with the check. The CLI SHALL offer `why` with the same output as plain text.

#### Scenario: Free-text question
- **WHEN** the investor writes "why do I own Google?"
- **THEN** the bot replies with the check for Alphabet

#### Scenario: CLI
- **WHEN** the investor runs the CLI `why TCS`
- **THEN** it prints the same verdict, rules, thesis and summary as plain text
