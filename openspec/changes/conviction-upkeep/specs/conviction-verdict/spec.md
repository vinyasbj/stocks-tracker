# Spec Delta

## MODIFIED Requirements

### Requirement: A held stock gets ADD, HOLD, REVIEW or SELL
A stock in the Notion portfolio SHALL get the first verdict that applies, in this order: SELL if a must-pass rule outside Valuation fails; REVIEW if a code-checked thesis breaker is hit; REVIEW if the scored share is below the bar; HOLD if a Valuation rule fails; HOLD if a must-pass rule or Valuation rule is unknown; otherwise ADD. An unknown result, or a possible breaker reported only by the model, SHALL NOT by itself cause SELL or REVIEW.

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

#### Scenario: Own thesis breaker hit
- **WHEN** a held stock passes every blueprint rule but its breaker "track: P/E > 35" is hit
- **THEN** the verdict is REVIEW, with the breaker as the reason

#### Scenario: Breaker and broken rule together
- **WHEN** a held stock fails a must-pass rule and also hits a thesis breaker
- **THEN** the verdict is SELL

#### Scenario: Possible breaker only
- **WHEN** the model reports a possible hit on an unmeasurable breaker and every blueprint rule passes
- **THEN** the verdict is ADD, with the possible breaker shown beside it
