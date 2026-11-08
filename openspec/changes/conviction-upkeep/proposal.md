# Proposal

## Why

`conviction-blueprint` lets the investor judge any stock on demand, but a thesis only stays useful if someone keeps it up to date. Today the investor would have to notice each quarter's results, re-run `/why`, copy the outcome into the stock's review log, and remember their own sell triggers and price levels. This change makes the bot do that upkeep. It writes into the investor's Notion pages, append-only, and speaks up only when something changes.

## What Changes

- **Append-only Notion writing**: the integration gains *Insert content* and nothing more. The bot adds lines and creates wishlist rows, and never edits or deletes the investor's text. Without the permission, every writing feature turns off with one explanation, and everything else keeps working.
- **Quarterly review**: when a new quarter appears in a holding's data, the bot re-evaluates the stock once and appends one line to the page's REVIEW LOG. The line holds the quarter, each pillar's status, any thesis breaker hit, and the verdict with its blueprint version.
- **Verdict-change alerts**: when a stock's verdict moves for business reasons (for example HOLD → SELL, or AVOID → BUY on the wishlist), the bot sends a Telegram alert naming the rules that changed. Moves caused only by price and valuation go into the morning digest instead of a push. Locking a new blueprint version sends one summary of the verdicts it changed.
- **Thesis tracking**: `track:` lines on pillars and thesis breakers that use a metric the bot knows are checked against the data. A breaker that is hit raises a held stock to **REVIEW** (a failing must-pass blueprint rule still means SELL). Lines the bot can't check, such as segment numbers like "Search revenue", are read against new results filings and news by the local model. They are reported as *possible* breakers with evidence, and never change the verdict.
- **Action-plan triggers in the digest**: "add below ₹X / $X", "trim above ₹X / $X", and "trim if it grows past N% of my portfolio" are checked daily. Portfolio weight uses a USD/INR rate so INR and USD holdings can be added together.
- **Entry and decision log**: when a stock first appears in the portfolio, the bot appends an entry snapshot (date, price, P/E, verdict, blueprint version). A new `/log TICKER BUY|ADD|TRIM|SELL QTY @ PRICE reason` command appends a dated line to the DECISION LOG.
- **Start a thesis**: for a stock with no Notion row, `/why` offers a button that creates a wishlist row pre-filled with the thesis template and the locked blueprint's tick boxes.
- New metric **Net cash** (cash minus total debt) for `track:` lines and blueprint rules.
- Step one is a spike that confirms Notion's API can insert a block *after a given block*, at the API version the bot uses. The design has a fallback if it can't.

## Capabilities

### New Capabilities

- `thesis-journal`: the bot's append-only writes to stock pages and the wishlist: review-log lines, entry snapshots, decision-log lines, and new thesis rows, plus the safety rules on those writes.
- `conviction-review`: when stocks are re-evaluated (new quarter, daily, new blueprint version), what is recorded, and which changes reach the investor as alerts or digest lines.
- `thesis-tracking`: reading and checking the measurable parts of a thesis: `track:` conditions on pillars and breakers, model-read evidence for the rest, and action-plan price and weight triggers.

### Modified Capabilities

- `conviction-verdict`: a held stock whose code-checked thesis breaker is hit gets REVIEW, ranked just below SELL.
- `conviction-blueprint`: the metric vocabulary gains Net cash.

(Both specs are introduced by `conviction-blueprint`.)

## Impact

- **Depends on** `conviction-blueprint` being implemented and archived first; the `conviction-verdict` delta modifies its spec.
- **Notion**: the integration needs the *Insert content* capability. New calls: append block children (with a position), and create a page in the wishlist data source.
- **Code**: `data/notion.py` (write calls), `analysis/thesis.py` (`track:` and action-plan parsing, section anchors), `analysis/verdict.py` (breaker step), `analysis/blueprint.py` (Net cash), `data/market.py` (cash field, USD/INR rate), `alerts/monitor.py` and `alerts/digest.py` (review pass, digest section), `engine.py`, `store.py` (review and verdict history), `bot/handlers.py` (`/log`, Start-thesis button), `cli.py`, `llm/prompts.py` (tracking-evidence prompt), README.
- **No new dependencies.**
