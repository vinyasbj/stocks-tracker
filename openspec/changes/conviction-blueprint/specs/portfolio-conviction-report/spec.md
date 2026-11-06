# Spec Delta

## Purpose

One view of how every stock the investor holds stands against their locked conviction blueprint. It shows which holdings need attention first and which have no written thesis yet.

## ADDED Requirements

### Requirement: `/report` judges every holding by the locked blueprint
`/report` SHALL evaluate every stock in the Notion portfolio, and only those, against the latest locked blueprint, with held-stock verdicts.

#### Scenario: Portfolio holdings
- **WHEN** the portfolio has 12 stocks and the investor sends `/report`
- **THEN** the report covers exactly those 12 stocks

#### Scenario: No blueprint locked
- **WHEN** no blueprint version is locked
- **THEN** `/report` gives no verdicts and explains how to set up and lock the blueprint

#### Scenario: No holdings
- **WHEN** the portfolio is empty
- **THEN** `/report` says there is nothing to report and points to the Notion portfolio and `/sync`

### Requirement: The report is grouped by verdict, most urgent first
The report SHALL start with the date, the blueprint version, and the count of holdings per verdict. Holdings SHALL then be grouped in the order SELL, REVIEW, HOLD, ADD. Each stock SHALL take one line: symbol, rules passed out of rules counted, and the short reason, naming up to three deciding rules.

#### Scenario: Summary line
- **WHEN** the report covers 12 holdings
- **THEN** it starts with a line such as "12 holdings: 1 sell · 2 review · 7 hold · 2 add" and names the blueprint version

#### Scenario: Stock line
- **WHEN** TCS fails the must-pass rule ROE ≥ 15 with 13.8%
- **THEN** its line under SELL reads like "TCS 7/10 · ROE 13.8% (need ≥ 15.0%)"

### Requirement: The report shows what needs the investor's attention
After the verdict groups, the report SHALL list the holdings that have no thesis and the holdings whose conviction level disagrees with their verdict.

#### Scenario: Missing theses
- **WHEN** GOOGL and TCS have no thesis in Notion
- **THEN** the report ends with "No thesis yet: GOOGL, TCS"

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
`/report` SHALL NOT write to Notion or record any log entry, and SHALL NOT call the local model. Running it twice in a row SHALL produce the same report when the data has not changed. The CLI SHALL offer `report` with the same content as plain text.

#### Scenario: Run twice
- **WHEN** the investor sends `/report` twice within a minute
- **THEN** both reports are the same and nothing is written anywhere

#### Scenario: Model not running
- **WHEN** Ollama is not running
- **THEN** `/report` still works in full
