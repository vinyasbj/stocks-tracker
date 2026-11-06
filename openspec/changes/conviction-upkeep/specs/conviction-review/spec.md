# Spec Delta

## Purpose

Re-evaluating stocks when their data or the blueprint changes. It decides what is recorded in each stock's review log, and which changes reach the investor at once, in the morning digest, or not at all.

## ADDED Requirements

### Requirement: A new quarter triggers one review per holding
On each monitor pass, the bot SHALL check whether Yahoo's latest quarter for each portfolio holding is newer than the last quarter reviewed for that stock. If it is, the bot SHALL evaluate the stock and record a review for that quarter, exactly once, even across restarts. A holding seen for the first time is reviewed for its current quarter. Out-of-date quarters SHALL NOT trigger a review.

#### Scenario: Results land in the data
- **WHEN** Yahoo adds the September 2026 quarter for TCS and the next monitor pass runs
- **THEN** TCS gets one review for September 2026

#### Scenario: Restart
- **WHEN** the bot restarts after reviewing TCS for September 2026
- **THEN** no second September 2026 review is recorded

#### Scenario: Stale data
- **WHEN** Yahoo's latest quarter for a stock is out of date
- **THEN** no review is triggered for that stock

### Requirement: The review is written to the review log
A review SHALL append one line to the holding's REVIEW LOG with: the date, the quarter, each pillar's status in order (met, not met, no data, or not measurable), the thesis breakers hit and possible breakers, the verdict, the blueprint version, and the passed/counted rule count. A holding without a thesis page gets no line written.

#### Scenario: Review line
- **WHEN** GOOGL is reviewed for September 2026 with pillars met, not measurable, met, not met, and no breakers
- **THEN** its REVIEW LOG gets a line like "🤖 2026-11-20 · Sep 2026: P1 ✅ P2 ❓ P3 ✅ P4 ⚠️ · breakers: none · HOLD (blueprint v1, 9/11)"

#### Scenario: Possible breaker from the model
- **WHEN** the model reports a possible hit on "Search revenue falls 2 quarters in a row"
- **THEN** the line lists it as a possible breaker, marked with "?"

### Requirement: Verdicts are tracked over time
The bot SHALL re-evaluate every portfolio and wishlist stock at each review and once a day before the morning digest, and SHALL keep each stock's latest verdict and rule results. The first evaluation of a stock, and the first after it moves between portfolio and wishlist, set a baseline and SHALL NOT count as a change.

#### Scenario: Baseline
- **WHEN** a stock is evaluated for the first time
- **THEN** its verdict is recorded and no change is reported

#### Scenario: Bought from the wishlist
- **WHEN** TCS moves from the wishlist (BUY) to the portfolio (ADD)
- **THEN** no verdict change is reported

### Requirement: Business verdict changes are alerted at once
When a stock's verdict differs from its recorded verdict and at least one rule outside the Valuation pillar, or a thesis breaker, changed status, the bot SHALL send a Telegram alert at once. The alert names the old and new verdict, each rule or breaker that changed with its old and new value, and the blueprint version.

#### Scenario: Holding breaks a rule
- **WHEN** TCS's new quarter brings ROE down to 13.8% and its verdict moves HOLD → SELL
- **THEN** the investor gets an alert "TCS: HOLD → SELL · ROE 15.4% → 13.8% (need ≥ 15.0%) · blueprint v1"

#### Scenario: Wishlist stock qualifies
- **WHEN** a wishlist stock's verdict moves AVOID → BUY because its profit CAGR now passes
- **THEN** the investor gets an alert

### Requirement: Price-only changes and action-plan triggers go into the digest
When a verdict changes and only Valuation rules changed status, the change SHALL NOT be pushed at once. It SHALL appear in a "Conviction" section of the next morning digest, together with any action-plan triggers that are met: add-below price, trim-above price, and trim-above portfolio weight. The section is omitted when it would be empty.

#### Scenario: P/E crosses the rule
- **WHEN** GOOGL's price falls so its P/E passes P/E ≤ 40 and its verdict moves HOLD → ADD
- **THEN** no immediate alert is sent, and the next digest says "GOOGL: HOLD → ADD (P/E now 38.2)"

#### Scenario: Add-below price hit
- **WHEN** GOOGL's action plan says "Add below $160" and the price is $158
- **THEN** the digest's Conviction section says GOOGL is below its add price

#### Scenario: Nothing to report
- **WHEN** no verdict changed and no trigger is met
- **THEN** the digest has no Conviction section

### Requirement: A new blueprint version is summarised, not alerted stock by stock
After `/blueprint lock` stores a new version, the bot SHALL re-evaluate every portfolio and wishlist stock and send one message listing the stocks whose verdict changed under the new version, then record those verdicts as the new baseline. It SHALL NOT send individual verdict-change alerts for these.

#### Scenario: Stricter bar
- **WHEN** v2 raises the pass bar and three holdings move HOLD → REVIEW
- **THEN** the lock reply is followed by one message listing those three, and no separate alerts are sent
