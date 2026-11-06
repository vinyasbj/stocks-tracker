# Spec Delta

## Purpose

The investor's written reason for owning each stock, kept in the stock's Notion page and read by the bot. It answers "why do I hold this?", supplies the answers to the blueprint's judgment rules, and carries the investor's own conviction level.

## ADDED Requirements

### Requirement: A stock's thesis is the body of its Notion portfolio row
The bot SHALL read a stock's thesis from the page body of its row in the Notion portfolio database. When a stock has several portfolio rows (several lots), the bot SHALL use the first row whose page has a thesis. The Notion wishlist SHALL NOT be read for theses.

#### Scenario: Holding with a thesis
- **WHEN** GOOGL's portfolio row has the thesis template filled in its page
- **THEN** `/check GOOGL` shows that thesis

#### Scenario: Several lots
- **WHEN** GOOGL has two portfolio rows and only the second row's page has a thesis
- **THEN** the bot uses the second row's thesis

#### Scenario: Stock not in the portfolio
- **WHEN** the investor checks a stock that is not in the Notion portfolio
- **THEN** the bot says the stock has no thesis yet, and that one is written in the stock's portfolio row using the thesis template once it is bought

#### Scenario: Row with an empty page
- **WHEN** a holding's page has no content
- **THEN** the bot treats the holding as having no thesis

### Requirement: Thesis sections are recognised by their headings
The bot SHALL split the page into sections by these headings, ignoring case and punctuation: One-line thesis, Why I own it, Entry, Risks I accept, Thesis breakers, Action plan, Review log, Decision log, Blueprint checks. A heading may be a Notion heading block or a paragraph whose whole text is the section name. Content under an unrecognised heading SHALL be kept with the section before it and SHALL NOT cause an error.

#### Scenario: Template pasted as Notion headings
- **WHEN** the page uses Notion headings "One-line thesis", "Why I own it" and "Thesis breakers", each followed by text or bullets
- **THEN** the bot reads each section's lines under its name

#### Scenario: Plain uppercase headings
- **WHEN** a section heading is a paragraph reading "THESIS BREAKERS (sell or re-think if)"
- **THEN** the bot recognises it as the Thesis breakers section

#### Scenario: Content in toggles and nested bullets
- **WHEN** a section's bullets are nested under a toggle heading or another bullet
- **THEN** the bot reads the nested lines as part of that section

### Requirement: The investor's conviction level is read from the thesis
The bot SHALL read a conviction level of HIGH, MEDIUM or LOW from a "Conviction" column on the stock's Notion row, or, when there is no such column, from a "Conviction:" line in the page. The column wins when both exist. A started date SHALL be read from a "Started:" line when present.

#### Scenario: Header line
- **WHEN** the page starts with "GOOGL — Alphabet   Conviction: HIGH   Started: 2026-11-06"
- **THEN** the bot reads conviction HIGH and started 2026-11-06

#### Scenario: No conviction given
- **WHEN** neither the row nor the page gives a conviction level
- **THEN** the conviction is shown as not set

### Requirement: Judgment rules are answered with tick boxes
A judgment rule SHALL count as confirmed for a stock only when the stock's page has a ticked tick box whose text matches the rule's text. Matching SHALL ignore case and punctuation and tolerate small wording differences. An unticked or missing tick box SHALL leave the rule unconfirmed.

#### Scenario: Ticked
- **WHEN** the page has a ticked tick box "I understand how the company makes money" and the blueprint has that judgment rule
- **THEN** the rule is confirmed for that stock

#### Scenario: Unticked
- **WHEN** the matching tick box is unticked
- **THEN** the rule is unconfirmed, and the bot says it is not ticked

#### Scenario: Missing
- **WHEN** no tick box on the page matches the rule
- **THEN** the rule is unconfirmed, and the bot says there is no tick box for it

### Requirement: Reading a thesis never changes Notion
The bot SHALL only read stock pages in this capability. It SHALL NOT create, edit or delete any Notion content.

#### Scenario: Read-only integration
- **WHEN** the Notion integration has only the "Read content" capability
- **THEN** every thesis feature in this capability works

### Requirement: A thesis that cannot be read does not block a verdict
When a stock's page cannot be read, the bot SHALL still evaluate the stock, treat its judgment rules as unconfirmed, and say the thesis could not be read.

#### Scenario: Page not shared with the integration
- **WHEN** Notion returns "not found" for a holding's page
- **THEN** `/check` shows a verdict with judgment rules unconfirmed and a note that the thesis could not be read
