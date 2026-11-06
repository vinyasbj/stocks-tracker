# Spec Delta

## Purpose

The bot's append-only writing into the investor's Notion: review-log lines, entry snapshots, decision-log lines and new thesis rows. These writes keep each thesis current without the bot ever changing what the investor wrote.

## ADDED Requirements

### Requirement: Writes are append-only
The bot SHALL only append new blocks to stock pages and create new rows in the wishlist database. It SHALL NOT edit, move or delete any existing block, page property or row.

#### Scenario: Insert-only integration
- **WHEN** the Notion integration has "Read content" and "Insert content" but not "Update content"
- **THEN** every feature in this capability works

#### Scenario: Investor's text untouched
- **WHEN** the bot appends a review line to a page
- **THEN** every block that existed before is unchanged

### Requirement: Missing write permission turns writing off, not the bot
When Notion refuses a write for lack of permission, the bot SHALL stop attempting writes, tell the investor once how to grant "Insert content", and keep every read-only feature working. Writes skipped this way SHALL NOT be retried after the permission is granted, except a review for the current quarter.

#### Scenario: Integration still read-only
- **WHEN** the first review-log write is refused by Notion
- **THEN** the investor gets one Telegram message explaining how to add "Insert content", and `/check`, `/report` and alerts keep working

#### Scenario: Permission granted later
- **WHEN** the investor adds "Insert content" and the next monitor pass runs
- **THEN** the current quarter's pending reviews are written, and nothing older is

### Requirement: Lines are added at the end of their section
A line SHALL be added after the last block of its target section (Review log, Entry or Decision log). When the page has no such section, the bot SHALL add the section heading at the end of the page, then the line.

#### Scenario: Section exists
- **WHEN** the REVIEW LOG section has two lines and the DECISION LOG section follows it
- **THEN** the new review line appears third in REVIEW LOG, above the DECISION LOG heading

#### Scenario: Section missing
- **WHEN** a page has no DECISION LOG heading and the investor logs a decision
- **THEN** a "Decision log" heading and the line are added at the end of the page

### Requirement: Bot-written lines are marked
Lines the bot composes (review lines and entry snapshots) SHALL start with "🤖", so the investor can tell them apart from their own writing. Decision-log lines from `/log` are the investor's words and SHALL NOT carry the mark.

#### Scenario: Review line
- **WHEN** the bot writes a review line
- **THEN** the line starts with "🤖"

### Requirement: An entry snapshot is recorded when a stock joins the portfolio
When a Notion sync finds a stock newly in the portfolio, the bot SHALL append one line under ENTRY: the date, price, P/E, verdict and blueprint version. It SHALL do this once per stock. Stocks already in the portfolio when this feature first runs SHALL NOT get one, since today's price is not their entry.

#### Scenario: New purchase
- **WHEN** the investor adds GOOGL to the Notion portfolio and the next sync runs
- **THEN** GOOGL's page gets "🤖 2026-11-06 · first seen in portfolio · $170.20 · P/E 24.1 · ADD (blueprint v1)" under ENTRY

#### Scenario: Feature deployed with existing holdings
- **WHEN** the feature runs for the first time with 12 holdings already in the portfolio
- **THEN** no entry snapshot is written for any of them

#### Scenario: Moved from wishlist to portfolio
- **WHEN** a wishlist stock is moved into the portfolio
- **THEN** it gets an entry snapshot, on whichever of its pages holds the thesis

### Requirement: The investor can log a decision from Telegram
`/log TICKER ACTION QTY @ PRICE REASON` SHALL append "DATE ACTION QTY @ PRICE — REASON" under the stock's DECISION LOG. ACTION is BUY, ADD, TRIM or SELL, and the price uses the stock's currency symbol. A message in the wrong form, or for a stock with no Notion page, SHALL write nothing and reply with the correct form. The CLI SHALL offer `log` with the same arguments.

#### Scenario: Logged
- **WHEN** the investor sends `/log GOOGL BUY 10 @ 170 Cloud margin turning`
- **THEN** GOOGL's DECISION LOG gets "2026-11-06 BUY 10 @ $170 — Cloud margin turning", and the bot confirms with a link to the page

#### Scenario: Wrong form
- **WHEN** the investor sends `/log GOOGL bought some`
- **THEN** nothing is written and the bot replies with the correct form and an example

#### Scenario: No Notion page
- **WHEN** the stock is in neither Notion list
- **THEN** nothing is written and the bot suggests the Start-thesis button on `/check`

### Requirement: A thesis can be started from `/check`
For a stock with no Notion row, `/check` SHALL offer a "Start thesis" button. The button SHALL create a wishlist row with the stock's ticker, name and exchange in the detected columns. The row's page holds the thesis template, headed with the stock's name and today's date, and has a BLUEPRINT CHECKS section built from the locked blueprint's judgment rules. The bot then syncs and replies with the page link.

#### Scenario: New stock
- **WHEN** the investor checks TCS, which is in neither list, and taps "Start thesis"
- **THEN** a TCS wishlist row exists with the template in its page, TCS is tracked as a wishlist stock, and the bot replies with the link

#### Scenario: Tapped twice
- **WHEN** the button is tapped again after the row was created
- **THEN** no second row is created and the bot replies with the existing page's link
