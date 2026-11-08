# Spec Delta

## Purpose

One view of how every stock the investor holds stands against their locked conviction blueprint. It shows which holdings need attention first and which have no written thesis yet.

## ADDED Requirements

### Requirement: `/report` judges every holding by the locked blueprint
`/report` SHALL first re-read the Notion portfolio, so rows added or removed since the last sync are reflected, and then evaluate every stock in it, and only those, against the latest locked blueprint, with held-stock verdicts. If Notion cannot be re-read, the report SHALL use the last synced list and say so. ETFs and mutual funds SHALL NOT get a verdict: company rules do not apply to them, so the report lists them as not judged.

#### Scenario: Portfolio holdings
- **WHEN** the portfolio has 12 stocks and the investor sends `/report`
- **THEN** the report covers exactly those 12 stocks

#### Scenario: Row removed in Notion
- **WHEN** the investor deletes a stock's row from the Notion portfolio and then sends `/report`
- **THEN** the report no longer includes that stock

#### Scenario: ETF holding
- **WHEN** the portfolio holds QQQM, an ETF
- **THEN** the report lists it under "Not judged (ETFs and funds)" with no verdict and no thesis reminder

#### Scenario: No blueprint locked
- **WHEN** no blueprint version is locked
- **THEN** `/report` gives no verdicts and explains how to set up and lock the blueprint

#### Scenario: No holdings
- **WHEN** the portfolio is empty
- **THEN** `/report` says there is nothing to report and points to the Notion portfolio and `/sync`

### Requirement: The report is grouped by verdict, most urgent first, as aligned tables
The report SHALL start with the date, the blueprint version, and the count of holdings per verdict (plus funds and unavailable stocks, when there are any). Holdings SHALL then be grouped in the order SELL, REVIEW, HOLD, ADD. Telegram has no tables, so each group SHALL be a monospace block with aligned columns, narrow enough for a phone (about 32 characters), and each rule's limit SHALL be stated once in a "Limits" line under the block rather than on every row:
- SELL: Stock · Rule broken · Value, one row per must-pass rule broken. A stock is listed under the first must-pass rule it breaks, with any others as "+Rule" rows below it. Stocks are grouped by that first rule, the rule broken by the most stocks first.
- REVIEW: Stock · Pass (scored rules passed out of scored) · Fails · Value, one row per scored rule failed.
- HOLD: holdings waiting on the same confirmation listed together under it (for example "Confirm 4 judgment rules"), as a grid of symbols; then "Too expensive to add" with each stock's Valuation rule and value.
- ADD, and ETFs and funds (not judged): a grid of symbols.

#### Scenario: Summary line
- **WHEN** the report covers 12 holdings
- **THEN** it starts with a line such as "12 holdings · 🔴 1 sell · 🟠 2 review · 🔵 7 hold · 🟢 2 add" and names the blueprint version

#### Scenario: Stocks breaking the same rule
- **WHEN** ORCL and VST both fail the must-pass rule Debt/Equity ≤ 1 with 3.67 and 3.94
- **THEN** the SELL table has the rows "ORCL  Debt/Equity  3.67" and "VST  Debt/Equity  3.94" next to each other, and "Debt/Equity ≤ 1.00" appears once, in the Limits line

#### Scenario: Holdings waiting on the same confirmation
- **WHEN** nine holdings are HOLD because their 4 judgment rules are not ticked
- **THEN** the HOLD block shows "Confirm 4 judgment rules" once, followed by the nine symbols in a grid

### Requirement: The report shows what needs the investor's attention
After the verdict groups, the report SHALL list the holdings that have no thesis, the holdings whose thesis page could not be read (kept apart, since their thesis may well exist), and the holdings whose conviction level disagrees with their verdict.

#### Scenario: Missing theses
- **WHEN** GOOGL and TCS have no thesis in Notion
- **THEN** the report ends with "No thesis yet: GOOGL, TCS"

#### Scenario: Thesis page not readable
- **WHEN** a holding's Notion page is not shared with the integration
- **THEN** the report lists it as "Thesis page unreadable", not as having no thesis

#### Scenario: Mismatch
- **WHEN** a holding's conviction is HIGH and its verdict is SELL
- **THEN** the report lists it as a conviction mismatch

### Requirement: The report needs no wishlist
The report and every other conviction feature SHALL work when no Notion wishlist database is configured. An empty `NOTION_WISHLIST_DB`, or one still set to the placeholder link from `.env.example`, SHALL count as not configured and SHALL NOT make the Notion sync fail.

#### Scenario: Placeholder left in `.env`
- **WHEN** `NOTION_WISHLIST_DB` is `https://www.notion.so/...` and the portfolio database is set
- **THEN** the portfolio syncs and `/report` covers every holding

### Requirement: One stock's failure does not stop the report
When a stock's data or thesis cannot be fetched, the report SHALL still cover every other stock and SHALL list that stock as unavailable, with the reason.

#### Scenario: Yahoo fails for one stock
- **WHEN** Yahoo returns no data for one of 12 holdings
- **THEN** the report covers the other 11 and lists the failed one as "data unavailable"

### Requirement: A long report is split, never cut
When the report is longer than one Telegram message allows, the bot SHALL send it as several messages split between lines, and SHALL NOT drop any stock.

#### Scenario: Forty holdings
- **WHEN** the report text exceeds the Telegram message limit
- **THEN** it arrives as two or more messages, and together they list every holding

### Requirement: The report is read-only and quick
`/report` SHALL NOT write to Notion or record any log entry (re-reading the portfolio updates only the bot's own synced list), and SHALL NOT call the local model. Running it twice in a row SHALL produce the same report when the data has not changed. The CLI SHALL offer `report` with the same content as plain text.

#### Scenario: Run twice
- **WHEN** the investor sends `/report` twice within a minute
- **THEN** both reports are the same and nothing is written anywhere

#### Scenario: Model not running
- **WHEN** Ollama is not running
- **THEN** `/report` still works in full
