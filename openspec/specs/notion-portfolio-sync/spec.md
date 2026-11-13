# notion-portfolio-sync Specification

## Purpose

Keep the Notion portfolio tidy as the investor buys and sells: sold stocks leave the portfolio, and every held stock's page shows the company logo.

## Requirements

### Requirement: Fully sold stocks are removed from Notion
A sync (`/sync`, the scheduled check, `cli sync`) SHALL treat a portfolio row whose quantity is exactly 0 as fully sold: it SHALL move that row's page to the Notion trash (restorable for 30 days) and SHALL NOT track the stock. A row with an empty quantity, and any wishlist row, SHALL NOT be removed. If Notion refuses the change, the sync SHALL log it and still finish.

#### Scenario: Stock sold
- **WHEN** the investor sets PANW's Qty to 0 in the Notion portfolio and the bot syncs
- **THEN** the PANW page is in the Notion trash and the bot no longer tracks PANW

#### Scenario: Quantity left empty
- **WHEN** a portfolio row has no quantity
- **THEN** the sync keeps the row and tracks the stock

### Requirement: Portfolio pages get the company logo
A sync SHALL give every portfolio page that has no icon the logo of the company's website (Google's favicon for the domain Yahoo Finance lists). An ETF or fund, which has no website on Yahoo, SHALL get its fund family's logo when the family is known. A page that already has an icon SHALL NOT be changed. When no logo can be found, the page SHALL stay without one and the sync SHALL still finish.

#### Scenario: New stock bought
- **WHEN** the investor adds a SHOP row to the Notion portfolio and the bot syncs
- **THEN** the SHOP page's icon is the shopify.com logo

#### Scenario: ETF
- **WHEN** the investor adds VTWO, a Vanguard ETF
- **THEN** its page icon is the vanguard.com logo

#### Scenario: Icon chosen by the investor
- **WHEN** a portfolio page already has an icon
- **THEN** the sync leaves it as it is

### Requirement: `/report` does not write to Notion
`/report` re-reads the portfolio but SHALL NOT trash rows or set icons, so it stays read-only.

#### Scenario: Report with a sold row
- **WHEN** a portfolio row has Qty 0 and the investor sends `/report`
- **THEN** the report leaves that stock out, and its Notion page is untouched until the next sync
