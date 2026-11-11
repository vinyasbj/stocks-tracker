# Design

## Context

What exists today and shapes this design:

- `analysis/metrics.py` already computes most numbers a blueprint needs: ROE, net margin, debt/equity, current ratio, P/E, revenue growth YoY and 3-year CAGR, free-cash-flow years, and distance from the 52-week high. It also detects stale Yahoo quarters (`quarter_stale`). `analysis/signals.py` applies fixed thresholds to them and knows which ratios don't apply to banks (`is_financial`).
- `data/notion.py` reads the portfolio and wishlist through a **read-only** internal integration, detecting columns by common names. Rows are matched to listed stocks in `sync.py` and mirrored into the SQLite `holdings` table every 15 minutes. **Notion page ids are not kept**, so the bot can't open a holding's page today.
- Commands share one pattern in `bot/handlers.py`: resolve the ticker (buttons when ambiguous), send a code-rendered message, then edit it once the local model's summary arrives (`do_analyze`). The CLI calls the same `Engine` methods.
- Prompts in `llm/prompts.py` forbid buy/sell advice, and the README says the bot gives no investment advice.

## Goals / Non-Goals

**Goals:**
- Verdicts are deterministic: the same locked blueprint and the same data always give the same verdict, without the model.
- Reuse the existing metrics, financial-sector handling and stale-quarter detection, rather than adding a second set of numbers.
- Keep the Notion integration read-only.
- `/report` over about 30 holdings finishes in well under a minute on a laptop.

**Non-Goals:**
- Writing to Notion, review logs, alerts, thesis breakers and `track:` lines checked against data, price triggers, portfolio weights: all in `conviction-upkeep`.
- Replacing `signals.py`. `/analyze` keeps its own strengths-and-weaknesses rules.
- A command to browse or roll back to old blueprint versions. Versions are stored and could be shown later.
- Currency conversion.

## Decisions

### 1. The lock lives in SQLite; Notion stays the editing surface
New table `blueprint_versions(version INTEGER PRIMARY KEY, locked_at REAL, fingerprint TEXT, rules TEXT)`, where `rules` is the parsed rule list as JSON. The latest row is the active blueprint. `/blueprint lock` parses Notion, compares fingerprints, and inserts a row only on a real change.

- *Fingerprint*: SHA-256 of the canonical JSON of the rules, sorted by pillar, must-pass and text, so that row order in Notion never counts as a change.
- *Diff*: rules are keyed by normalised rule text. A rule present in both versions with a different metric, condition, threshold, pillar or must flag counts as "changed".
- *Alternatives*: Notion's own "Lock database" toggle can be switched off silently and isn't visible through the API, so it only complements the bot's lock (the README suggests it). A YAML file in the repo would be versioned by git, but the investor chose Notion as the place to edit.

### 2. Rules: one shape, two kinds, plus a settings row
A rule has `text, pillar, metric, op, threshold, must`. When `metric == "judgment"` the condition and threshold are unused. The Settings pillar is not a rule: its row "Scored rules pass bar" is stored as a `pass_bar` field beside the rules in the same locked JSON, so it is fingerprinted and versioned with them (default 75).

Parsing is deliberately tolerant on *form*, so that CSV-imported text columns, checkbox or "Yes", and "≥" or ">=" or "at least" all work. It is strict on *meaning*: an unknown metric, a missing threshold, an unclear condition, or a fraction in a percentage rule is reported and blocks locking. Column detection reuses the holdings approach: `notion.find_columns()` is generalised from `pick_columns()`, taking a candidate map.

### 3. A metric registry over the existing metrics
`analysis/blueprint.py` holds a registry: key → label, unit (`%`, `x`, `years`), getter `(metrics, fundamentals) -> value`, aliases, and `not_for_financials`. Getters read from `metrics.compute()` output or the fundamentals dict, so no number is computed twice. `metrics.compute()` gains three fields: `ni_cagr` (profit CAGR, the same method as `rev_cagr`), `op_margin` (Yahoo's TTM operating margin, falling back to the latest quarter's), and `loss_years` (the number of consecutive fiscal years, counting back from the latest, with a net loss; `None` without annual results). `loss_years` backs the metric "Consecutive loss years", which encodes the investor's rule "no company loss-making in each of its last 2 years" as `≤ 1`. One loss year passes that rule on its own; the margin and ROE rules usually catch it.

*Alternative*: free-form formulas in Notion such as "ROE - 2*D/E". Rejected: hard to validate, and a typo could silently change verdicts.

### 4. Evaluation and verdict are pure functions
`evaluate(rules, metrics, fundamentals, todos) -> list[Check]` and `decide(checks, held, pass_bar) -> Verdict` live in `analysis/verdict.py`. They take plain data and do no I/O, so the whole verdict table in the specs can be unit-tested without network or Notion. The order of checks is exactly the one in `specs/conviction-verdict`.

"Held" means present in the Notion portfolio (`Holding.list_type == "portfolio"`). Any other stock gets the not-held verdicts; the wishlist plays no part in this change.

### 5. Theses: page ids flow through the sync
- `NotionRow` gains `page_id` and an optional `conviction` column value. `Holding` gains `pages: tuple[str, ...]` and `conviction`.
- `Store.replace_holdings()` merges page ids when portfolio lots merge, in Notion order. Wishlist rows contribute no page ids, so a wishlist page is never read as a thesis.
- `holdings` gains `pages TEXT` and `conviction TEXT` columns. Existing databases are migrated with `ALTER TABLE … ADD COLUMN`, ignoring "duplicate column".
- `notion.page_blocks(token, page_id)` fetches `GET /v1/blocks/{id}/children` with pagination, recursing into children of headings, toggles, list items and columns up to depth 3, and flattening in reading order.
- `analysis/thesis.py` turns blocks into a `Thesis`: a section name → lines map, a list of (to-do text, ticked) pairs, conviction, and started date. A heading is a heading block, or a paragraph whose text normalises to a known section name. Normalising drops anything in parentheses, so "THESIS BREAKERS (sell or re-think if)" still matches.
- To-do matching takes the best of `rapidfuzz.fuzz.ratio` and `token_sort_ratio` on normalised text, with a threshold of 85. `token_set_ratio` was rejected because it scores 100 whenever one text's words are a subset of the other's, so a tick box reading just "management" would confirm "Management is honest and allocates capital well". `rapidfuzz` is already a dependency.

*Alternative*: a separate "Theses" database linked by relation. Rejected: one more database to share and keep in sync, when the investor already has one row per stock.

### 6. The model explains, it does not decide
A new `CONVICTION_SYSTEM` prompt receives the existing `report.data_block()`, plus the rule results, the verdict line and the thesis text. It asks for 2–3 sentences: what failed or couldn't be checked and why it matters (not the rules that pass, which are shown above), whether recent results and news support or challenge the verdict, and one thing to watch. It mentions a thesis only when there is one. It must not repeat or change the verdict, which is shown above it. A 7B model overruns sentence limits, so the code also keeps only the first three sentences. The grounding text is split, so this prompt keeps "use only DATA" and "plain text" but drops "do not tell the user to buy or sell"; that sentence stays in every other prompt.

`/why` uses the existing send-then-edit pattern, so the verdict appears before the model finishes.

### 7. Report: concurrent, model-free, split on lines
The engine evaluates holdings with `asyncio.gather`, through `asyncio.to_thread`, limited by a semaphore of 4. Fundamentals are already cached for 6 hours in `market.fetch_fundamentals`, so repeated reports are fast. Each stock's failure is caught and becomes an "unavailable" line. Rendering produces whole lines, and a `split_messages(lines, limit=4000)` helper packs them into several messages. The existing `report.clip()` truncates, which the spec forbids here.

### 8. Commands, intent, CLI
- Handlers: `/why` maps to a new action code `ck` through the existing `command()` and `pick()`; buttons reuse the `ck|SYMBOL|MARKET` callback. `/report` and `/blueprint [lock]` are plain handlers. All are added to `COMMANDS` and `HELP`.
- `Intent.intent` gains `"check"`. `INTENT_SYSTEM` describes it as buy/sell/hold/add questions, fit with "my rules / blueprint / conviction", and "why do I own …". `on_text` dispatches it to the check.
- The CLI gains `check`, `why`, `report`, `blueprint [lock]`.

### 9. Module layout
| Module | Responsibility |
|---|---|
| `analysis/blueprint.py` | metric registry, `Rule`, Notion row parsing, fingerprint, diff |
| `analysis/verdict.py` | `Check`, `evaluate`, `Verdict`, `decide` |
| `analysis/thesis.py` | blocks → `Thesis` |
| `analysis/conviction.py` | Telegram rendering for `/why`, `/report`, `/blueprint` |
| `engine.py` | `blueprint_status()`, `lock_blueprint()`, `conviction(sec)`, `business_profile(sec, research)`, `conviction_report()` |

### 10. Business profile in `/why`
- **Source**: Yahoo's `longBusinessSummary` and `country`, added to `INFO_FIELDS` as `business_summary` and `country`, and cached with the fundamentals. Checked live: GOOGL's description names its segments and regions; TCS's lists platforms and services. yfinance exposes **no segment or geographic revenue/profit numbers**, so the profile never shows a split and says so.
- **How and where**: a `PROFILE_SYSTEM` prompt condenses the description into at most 3 lines (business lines or segments, customers, regions) and allows no numbers. *Guard*: if the output contains any digit sequence absent from the description, the bot uses the first ~300 characters of the description instead. The model's text is cached for 30 days under `profile:<market>:<symbol>:<hash of description>`, so only the first `/why` of a stock pays for this extra model call.
- **Profit**: rendered by code from `metrics.compute()`: `q_net_income`, `ni_yoy`, `ni_qoq`, the latest annual `net_income`, `ni_cagr`, `net_margin`, `op_margin`. A negative latest quarter renders as "Loss-making: net loss …".
- *Alternatives*: SEC XBRL segment facts (US only; the companyfacts API omits dimensional data) and Indian annual-report PDFs (no structured source). Both are out of scope; a later change could add them.

### 11. No dependency on a wishlist
`config.load_settings()` treats `NOTION_WISHLIST_DB` as unset unless it contains a Notion id (32 hex characters), so the `.env.example` placeholder no longer breaks `notion.db_id()` during sync. It logs once that the wishlist is off. Conviction code only reads portfolio holdings.

### 12. Starter blueprint (templates/blueprint.csv)
The investor edits this in Notion before the first lock.

| Pillar | Rule | Metric | Cond. | Threshold | Must |
|---|---|---|---|---|---|
| Business | I understand how the company makes money | Judgment | | | yes |
| Business | It has a durable moat (brand, network, cost or switching costs) | Judgment | | | yes |
| Business | Management is honest and allocates capital well | Judgment | | | yes |
| Business | No promoter pledging or governance red flags | Judgment | | | yes |
| Quality | Profit-making: not loss-making in each of the last 2 years | Consecutive loss years | ≤ | 1 | yes |
| Quality | Return on equity at least 15% | ROE | ≥ | 15 | yes |
| Quality | Net profit margin at least 8% | Net margin | ≥ | 8 | |
| Quality | Positive free cash flow in at least 3 years | FCF positive years | ≥ | 3 | |
| Growth | Revenue grew at least 10% a year over 3 years | Revenue CAGR 3Y | ≥ | 10 | |
| Growth | Profit grew at least 10% a year over 3 years | Profit CAGR 3Y | ≥ | 10 | |
| Growth | Latest quarter revenue up year on year | Revenue growth YoY | ≥ | 0 | |
| Growth | Latest quarter profit up year on year | Profit growth YoY | ≥ | 0 | |
| Safety | Debt/equity at most 1 | Debt/Equity | ≤ | 1 | yes |
| Safety | Current ratio at least 1.2 | Current ratio | ≥ | 1.2 | |
| Valuation | P/E at most 40 | P/E | ≤ | 40 | |
| Settings | Scored rules pass bar | | | 75 | |

`templates/thesis.md` is the investor's template, with Notion `##` headings so it pastes as heading blocks, plus a BLUEPRINT CHECKS section whose tick boxes match the starter judgment rules.

## Risks / Trade-offs

- [Notion percent-formatted numbers store 0.15 for 15%] → Rejected as an invalid row with a clear message; the README and CSV use plain numbers.
- [Rewording a judgment rule breaks the tick-box match on every page] → Fuzzy matching absorbs small edits. A larger rewording shows as "no tick box for it" (unknown, never SELL), and `/blueprint` prints the new block to paste.
- [Notion's rate limit, about 3 requests per second, during `/report`; nested blocks multiply calls] → The semaphore of 4 plus a retry on HTTP 429 that honours `Retry-After`, added to the Notion client; depth limited to 3.
- [The 7B model contradicts the verdict in its summary] → The verdict is rendered by code above the summary and labelled as the blueprint's. The prompt forbids changing it. The summary is commentary only.
- [Stale Yahoo statements, e.g. HDFC Bank] → YoY rules become unknown, not failed, so a stale feed can never cause a SELL.
- [Starter rules may not suit the investor or a sector (e.g. P/E 40 vs Indian quality stocks)] → Nothing is judged until the investor reviews and locks. Rule edits are a deliberate new version.
- [Mixing a bank's not-applicable rules makes counts differ between stocks] → Every line shows "passed/counted", never a bare score.
- [The model invents segments or regions in the profile] → It may only use the description; any stray number triggers the raw-description fallback; the section is labelled "from Yahoo's company description".
- [Some descriptions list products rather than segments (e.g. TCS)] → Shown as is, with the label. A real segment split needs filings, which is a later change.
- [Yahoo annual history covers only 4–5 years, and Indian companies sometimes report in USD (INFY.NS)] → The loss streak uses the sign of net income only, so currency doesn't matter. With no annual rows the rule is unknown, never failed.
- [Verdict wording reads like advice] → Every verdict message says it applies the investor's own blueprint vN. The disclaimer is reworded rather than removed.

## Migration Plan

1. Deploy: the `ALTER TABLE` migration runs on start-up. The first Notion sync afterwards fills page ids and conviction values (within 15 minutes, or immediately with `/sync`).
2. The investor imports `templates/blueprint.csv` into Notion, shares the database with the integration, sets `NOTION_BLUEPRINT_DB`, restarts, reviews with `/blueprint`, and runs `/blueprint lock`.
3. Rollback: older code ignores the new table and columns. No data is rewritten.
