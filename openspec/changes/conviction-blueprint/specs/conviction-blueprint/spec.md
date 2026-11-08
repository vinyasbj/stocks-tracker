# Spec Delta

## Purpose

The investor's own fixed rule set for judging stocks: defined in Notion, validated, and locked into numbered versions. Every stock is judged by the same rules, which change only when the investor deliberately locks a new version.

## ADDED Requirements

### Requirement: Rules are defined in a Notion database
The bot SHALL read the blueprint from the Notion database set in `NOTION_BLUEPRINT_DB`. Each row is one rule with these columns: Rule (text), Pillar (Business, Quality, Growth, Safety, Valuation, or Settings), Metric, Condition (≥ or ≤), Threshold, and Must pass. Columns SHALL be found by common names, and text-typed columns SHALL be accepted, so that a database imported from the starter CSV works without changing column types.

#### Scenario: Numeric rule
- **WHEN** a row has Rule "Return on equity at least 15%", Pillar "Quality", Metric "ROE", Condition "≥", Threshold 15, and Must pass ticked
- **THEN** the bot reads a must-pass numeric rule requiring ROE ≥ 15%

#### Scenario: Judgment rule
- **WHEN** a row's Metric is empty or "Judgment"
- **THEN** the bot reads it as a judgment rule that the investor answers per stock, and ignores Condition and Threshold

#### Scenario: Columns imported as text
- **WHEN** the database was imported from CSV, so Threshold is text "15" and Must pass is text "Yes"
- **THEN** the bot reads Threshold as 15 and Must pass as true

#### Scenario: Empty and deleted rows
- **WHEN** a row has no Rule text, or is in the Notion trash
- **THEN** the bot ignores it

### Requirement: Numeric rules use a fixed metric vocabulary
A numeric rule's Metric SHALL name one of: ROE, Net margin, Operating margin, Debt/Equity, Current ratio, P/E, Forward P/E, P/B, Revenue growth YoY, Profit growth YoY, Revenue CAGR 3Y, Profit CAGR 3Y, FCF positive years, Consecutive loss years, Dividend yield, From 52w high. Common spellings (for example "Return on equity", "D/E", "PE ratio") SHALL be accepted. Percentages are written as plain numbers: 15 means 15%.

#### Scenario: Alias accepted
- **WHEN** a row's Metric is "Return on equity"
- **THEN** the bot reads it as ROE

#### Scenario: Unknown metric
- **WHEN** a row's Metric is "Promoter holding"
- **THEN** the bot does not use the row and reports it as a problem naming the unknown metric

#### Scenario: Consecutive loss years
- **WHEN** a company reported a net loss in its latest fiscal year and the year before, and a profit the year before that
- **THEN** its Consecutive loss years value is 2

### Requirement: Invalid rows are reported, never guessed
The bot SHALL report every row it cannot use, with the reason, and SHALL NOT guess a missing or unclear value.

#### Scenario: Missing threshold
- **WHEN** a numeric rule has no Threshold
- **THEN** the row is reported as "threshold is missing"

#### Scenario: Unclear condition
- **WHEN** a numeric rule's Condition is "between"
- **THEN** the row is reported as needing ≥ or ≤

#### Scenario: Strict condition
- **WHEN** a numeric rule's Condition is ">", "<", "above" or "below"
- **THEN** the row is reported as needing ≥ or ≤, and is not read as "or equal"

#### Scenario: Unknown pillar
- **WHEN** a row's Pillar is empty or not one of Business, Quality, Growth, Safety, Valuation or Settings (for example "Qualty")
- **THEN** the row is reported with the allowed pillars, and is not used

#### Scenario: Percentage written as a fraction
- **WHEN** a percentage rule's Threshold is 0.15, as a Notion percent-formatted number stores it
- **THEN** the row is reported, asking for 15 in a plain number column

### Requirement: The scored-rule pass bar is part of the blueprint
A row in the Settings pillar named "Scored rules pass bar" SHALL set the share of scored rules a stock must pass, as a percentage. When no such row exists the bar SHALL be 75%. The bar is locked and versioned with the rules.

#### Scenario: Bar set in Notion
- **WHEN** the Settings row "Scored rules pass bar" has Threshold 70 and the blueprint is locked
- **THEN** that version judges stocks with a 70% bar

#### Scenario: No settings row
- **WHEN** the blueprint has no Settings row
- **THEN** the bar is 75%

### Requirement: Stocks are judged only by a locked version
The bot SHALL judge stocks only by the latest locked version of the blueprint, never directly by the current Notion contents. Each locked version has a number (v1, v2, …) and a lock date.

#### Scenario: Nothing locked yet
- **WHEN** the investor sends `/why TCS` before any blueprint is locked
- **THEN** the bot gives no verdict and explains how to set up the blueprint in Notion, review it with `/blueprint`, and lock it with `/blueprint lock`

#### Scenario: Notion edited after locking
- **WHEN** v1 is locked, the investor then changes a threshold in Notion, and sends `/why TCS`
- **THEN** the verdict uses v1, and the reply notes that Notion has unlocked edits and how to adopt them

#### Scenario: Notion unreachable
- **WHEN** a blueprint is locked and Notion cannot be reached
- **THEN** `/why` and `/report` still work with the locked version

### Requirement: Locking creates a new version on purpose
`/blueprint lock` SHALL read the blueprint from Notion and store it as the next version, but only when it differs from the latest locked version and has no invalid rows. The reply SHALL list what changed. Reordering rows in Notion SHALL NOT count as a change. Earlier versions SHALL be kept.

#### Scenario: First lock
- **WHEN** no version exists and the investor sends `/blueprint lock`
- **THEN** the bot stores v1 and replies with its rules

#### Scenario: Lock after edits
- **WHEN** v1 is locked and the investor changed one rule and added one in Notion
- **THEN** `/blueprint lock` stores v2 and replies "1 changed, 1 added", showing the old and new form of the changed rule

#### Scenario: Nothing changed
- **WHEN** Notion matches the latest locked version, apart from row order
- **THEN** `/blueprint lock` stores no new version and says the blueprint is unchanged

#### Scenario: Invalid rows block the lock
- **WHEN** Notion contains a row the bot cannot use
- **THEN** `/blueprint lock` stores nothing and lists the rows to fix

### Requirement: The investor can view the blueprint
`/blueprint` SHALL show the locked version's number and lock date, its rules grouped by pillar with each rule's condition and must-pass flag, the pass bar, whether Notion has unlocked edits (with a short summary of them), and a ready-to-paste BLUEPRINT CHECKS block with one tick box per judgment rule.

#### Scenario: View with unlocked edits
- **WHEN** v1 is locked and Notion has two changed rules
- **THEN** `/blueprint` shows v1's rules and a notice "Notion differs: 2 changed. Still using v1. To adopt: /blueprint lock"

#### Scenario: Tick-box block
- **WHEN** the locked blueprint has three judgment rules
- **THEN** `/blueprint` ends with a BLUEPRINT CHECKS block containing three unticked tick boxes, each with a judgment rule's exact text
