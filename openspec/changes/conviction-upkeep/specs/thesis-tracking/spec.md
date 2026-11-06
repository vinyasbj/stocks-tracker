# Spec Delta

## Purpose

Checking the measurable parts of the investor's thesis: pillar and breaker conditions written as `track:` lines, model-read evidence for conditions the data can't measure, and the action plan's price and portfolio-weight triggers.

## ADDED Requirements

### Requirement: `track:` conditions are read from pillars and breakers
A line in the Why I own it or Thesis breakers section SHALL be measurable when it contains "track:" followed by a metric from the blueprint vocabulary, a comparison (>, ≥, >=, <, ≤, <=, above, below), and a number, optionally with "%". Any other line in those sections is not measurable.

#### Scenario: Measurable pillar
- **WHEN** a pillar reads "Not expensive → track: P/E < 35"
- **THEN** the bot reads the condition P/E < 35 for that pillar

#### Scenario: Segment metric
- **WHEN** a pillar reads "Search keeps growing → track: Search revenue YoY > 8%"
- **THEN** the pillar is not measurable, because "Search revenue YoY" is not in the vocabulary

#### Scenario: No number
- **WHEN** a line reads "track: Cloud operating margin rising"
- **THEN** the line is not measurable

### Requirement: Measurable pillars and breakers are checked against the data
A measurable pillar SHALL be "met" when its condition holds, "not met" when it does not, and "no data" when the value is missing. A measurable breaker SHALL be "hit" when its condition holds, "clear" when it does not, and "no data" when the value is missing.

#### Scenario: Pillar not met
- **WHEN** the pillar condition is P/E < 35 and the P/E is 41
- **THEN** the pillar is not met, showing 41

#### Scenario: Breaker hit
- **WHEN** the breaker "P/E above 35 with no growth → track: P/E > 35" is checked and the P/E is 41
- **THEN** the breaker is hit, and the held stock's verdict is at least REVIEW

### Requirement: Unmeasurable lines are read against new filings and news
For each unmeasurable pillar and breaker, the local model SHALL read the stock's results filings and news since the previous review. It reports "supports", "challenges" or "no evidence", with the source and a short quote. A breaker the model says is hit SHALL be shown as a *possible* breaker and SHALL NOT change the verdict. When the model is unavailable, these lines SHALL be shown as "not checked".

#### Scenario: Possible breaker
- **WHEN** a results filing says Search revenue fell for the second quarter running, and a breaker reads "Search revenue falls 2 quarters in a row"
- **THEN** the breaker is shown as a possible breaker, citing the filing, and the verdict is unchanged

#### Scenario: Nothing new
- **WHEN** there are no results filings or relevant news since the previous review
- **THEN** each unmeasurable line shows "no evidence"

### Requirement: Action-plan triggers are read from the thesis
The bot SHALL recognise these action-plan lines: "add below PRICE", "trim above PRICE", and "trim if it grows past N% of my portfolio" (also "trim above N% of portfolio"). PRICE may carry ₹ or $ and SHALL be compared in the stock's trading currency. Other lines are shown as written and not checked.

#### Scenario: Add-below price
- **WHEN** the action plan reads "Add below $160 · Trim if it grows past 15% of my portfolio"
- **THEN** the bot reads an add-below price of 160 and a trim weight of 15%

#### Scenario: Placeholder price
- **WHEN** the action plan still reads "Add below ₹/$ X"
- **THEN** no add-below price is read

### Requirement: Portfolio weight counts INR and USD holdings together
A holding's weight SHALL be its market value divided by the total market value of all portfolio holdings with a quantity, after converting USD values to INR at Yahoo's current USD/INR rate. When the rate can't be fetched, weight triggers SHALL be skipped and the digest SHALL say so.

#### Scenario: Mixed currencies
- **WHEN** the portfolio holds ₹8,00,000 of Indian stocks and $2,000 of GOOGL, and USD/INR is 84
- **THEN** GOOGL's weight is 168,000 ÷ 968,000 = 17.4%, which meets a 15% trim trigger

#### Scenario: Rate unavailable
- **WHEN** the USD/INR rate can't be fetched
- **THEN** no weight trigger fires, and the digest notes that weights were not checked

### Requirement: `/check` shows the thesis tracking
For a stock with a thesis, `/check` SHALL show each pillar's status (met, not met, no data, or not measurable with the model's reading), each breaker's status (hit, clear, no data, or possible), and the action plan's triggers with the current price or weight.

#### Scenario: Check with tracking
- **WHEN** the investor sends `/check GOOGL` and its thesis has four pillars, three breakers and an add-below price
- **THEN** the reply lists the four pillar statuses, the three breaker statuses, and the add-below price beside the current price
