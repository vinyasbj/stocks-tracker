# Proposal

## Why

The investor holds stocks without a written, checkable reason for owning each one, and the bot judges stocks with fixed thresholds hard-coded in `analysis/signals.py` that the investor never chose. They need a single set of investing rules of their own, the *conviction blueprint*, that judges every stock the same way. It should not drift with the market. It should answer "should I buy this?" for a new stock, "should I still hold this?" for every holding, and "why do I own this?" for any of them.

## What Changes

- New **conviction blueprint**: the investor's rules live in a Notion database. A rule is either a *numeric rule* (a metric, ≥ or ≤, a threshold) or a *judgment rule* that the investor answers per stock. Any rule can be marked *must pass*. A settings row holds the scored-rule pass bar, 75% to start. The starter blueprint includes the investor's profitability rule as must-pass: **no company that has been loss-making in each of its last 2 fiscal years**.
- **Lock and versioning**: the bot judges stocks only by a locked snapshot (v1, v2, …). Editing the Notion database changes nothing until the investor sends `/blueprint lock`. The bot then shows what changed and stores a new version. Every verdict names the version that made it.
- **Stock thesis read from Notion**: each stock's thesis is the body of its row in the Notion portfolio, written in the investor's template: one-line thesis, pillars, entry, risks, thesis breakers, action plan, review log, decision log, and a BLUEPRINT CHECKS section of tick boxes, one per judgment rule. The bot reads it; in this change it does not write anything.
- **`/check TICKER`** (alias **`/why TICKER`**): evaluates any listed stock against the locked blueprint. It shows each rule as pass / fail / unknown / not applicable, gives a verdict, shows the stock's thesis when one exists, and adds a short explanation written by the local model. The verdict for a stock not in the portfolio is **BUY / WATCH / AVOID**; for a portfolio holding it is **ADD / HOLD / REVIEW / SELL**. Code decides the verdict; the model only explains it.
- **Business profile in `/check`**: *how and where the company makes its money*. The local model condenses Yahoo's company description into business lines, customers and regions. Below it, *profit numbers computed by code*: latest-quarter net profit, profit growth YoY and QoQ, latest full-year profit, profit CAGR 3Y, and net and operating margin. A loss-making company is shown as such. Yahoo has no segment-wise or country-wise profit split, so the bot says so instead of estimating one.
- **Conviction mismatch**: the investor's own conviction level (HIGH / MEDIUM / LOW, from the thesis) is shown beside the verdict, and the bot flags a disagreement such as HIGH conviction with a SELL verdict.
- **`/report`**: evaluates every portfolio holding and sends a summary grouped by verdict, plus the holdings that have no thesis yet. Read-only; numbers only, no model call per stock.
- **`/blueprint`**: shows the locked rules, whether Notion has unlocked edits, and the BLUEPRINT CHECKS tick-box block to paste into stock pages.
- Free-text questions such as *"should I buy TCS?"* or *"why do I own Google?"* route to `/check`.
- CLI equivalents: `check`, `why`, `report`, `blueprint [lock]`.
- Repository templates: a starter blueprint CSV to import into Notion, and the thesis page template.

Out of scope: any writing to Notion, review logs, verdict-change alerts, thesis breakers and `track:` lines checked against data, price triggers, and portfolio-weight checks. All of these are in the follow-up change `conviction-upkeep`. The Notion **wishlist is not used** by this change: theses, verdicts and reports cover the portfolio, and `/check` works for any listed stock without a Notion row. The bot must also keep working when no wishlist database is configured, including when the `.env` still has the placeholder link.

## Capabilities

### New Capabilities

- `conviction-blueprint`: the investor's rule set: how rules are defined in Notion, validated, locked into versions, and shown.
- `stock-thesis`: reading a stock's thesis and its judgment-rule tick boxes from the stock's Notion page.
- `conviction-verdict`: evaluating one stock against the locked blueprint and deciding its verdict, through `/check`, `/why`, free text and the CLI.
- `portfolio-conviction-report`: running the blueprint over every holding and reporting the results grouped by verdict.

### Modified Capabilities

(none: no specs exist yet)

## Impact

- **Code**: new rule/verdict and thesis modules under `src/stockbot/analysis/`. Changes to `data/notion.py` (page ids on rows, page-body reading, generic column detection), `sync.py` and `store.py` (holdings keep their Notion page ids; new table of locked blueprint versions; migration for existing databases), `analysis/metrics.py` (profit CAGR, operating margin, consecutive loss years), `data/market.py` (company description and country), `engine.py`, `bot/handlers.py`, `__main__.py` (commands), `cli.py`, `llm/prompts.py` (explanation and business-profile prompts, `check` intent), `config.py` (an empty or placeholder `NOTION_WISHLIST_DB` counts as not set).
- **Config**: new `NOTION_BLUEPRINT_DB` in `.env`. The Notion integration stays read-only; the blueprint database must be shared with it.
- **Docs**: README sections on the blueprint, theses, and the new commands. The README's "does not give investment advice" line is reworded: verdicts apply the investor's own rules and are not the bot's advice.
- **No new dependencies.** Uses `httpx`, `rapidfuzz`, `pydantic` and SQLite, which the project already has.
- **Follow-up**: `conviction-upkeep` depends on this change being archived first.
