# Spec Delta

## MODIFIED Requirements

### Requirement: Numeric rules use a fixed metric vocabulary
A numeric rule's Metric SHALL name one of: ROE, Net margin, Operating margin, Debt/Equity, Current ratio, P/E, Forward P/E, P/B, Revenue growth YoY, Profit growth YoY, Revenue CAGR 3Y, Profit CAGR 3Y, FCF positive years, Dividend yield, From 52w high, Net cash. Common spellings (for example "Return on equity", "D/E", "PE ratio") SHALL be accepted. Percentages are written as plain numbers: 15 means 15%. Net cash is cash minus total debt in the statement currency; positive means more cash than debt.

#### Scenario: Alias accepted
- **WHEN** a row's Metric is "Return on equity"
- **THEN** the bot reads it as ROE

#### Scenario: Unknown metric
- **WHEN** a row's Metric is "Promoter holding"
- **THEN** the bot does not use the row and reports it as a problem naming the unknown metric

#### Scenario: Net cash rule
- **WHEN** a rule is Net cash ≥ 0 and the company has $95B cash and $28B total debt
- **THEN** the rule passes, showing net cash of $67B

#### Scenario: Net cash for a bank
- **WHEN** the rule is Net cash ≥ 0 and the stock is a bank
- **THEN** the rule is not applicable
