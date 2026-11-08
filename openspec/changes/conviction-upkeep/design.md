# Design

## Context

Builds directly on `conviction-blueprint`. Its pieces this change extends are: the locked blueprint in `blueprint_versions`, `evaluate()` / `decide()` in `analysis/verdict.py`, the `Thesis` parser in `analysis/thesis.py`, holdings carrying Notion page ids, and `Engine.conviction()`.

Existing hooks this change plugs into:
- `Engine.check()` runs every `POLL_MINUTES` (15): it syncs Notion, then `Monitor.run()` alerts on new filings and news. Results filings are already tagged `results` (NSE and EDGAR).
- `handlers.digest_job` builds the 08:30 IST digest from `digest.build()`, which already fetches a fresh quote per holding (`Position`).
- `Monitor.new_items()` has a "first pass records a baseline" pattern over the `seen_items` table. This change reuses it for "don't backfill on first run".
- Fundamentals are cached for 6 hours, so a new Yahoo quarter is noticed within about 6 hours plus one poll.

Notion API facts (checked against the API reference): append-block-children takes `position: {"type": "after_block", "after_block": {"id": …}}` (the older `after` is deprecated), takes at most 100 blocks per call, and returns **403** without the *Insert content* capability.

## Goals / Non-Goals

**Goals:**
- Never lose or alter the investor's text. Every write is an insert.
- Each review, entry snapshot and alert happens at most once, across restarts and failures.
- Only push what changed the business picture; price noise waits for the digest.
- Writing must never break reading: every read feature from `conviction-blueprint` keeps working without the new permission.

**Non-Goals:**
- Editing or "fixing" a thesis, closing out a decision, or computing realised P&L from the decision log.
- Segment-level data sources. Unmeasurable lines stay model-read evidence.
- Hysteresis on valuation rules (see Risks).
- Writing for stocks outside the two Notion lists.

## Decisions

### 1. Notion writes: three calls, one permission switch
`data/notion.py` gains:
- `append_blocks(token, parent_id, blocks, after=None)`: PATCH `/blocks/{parent}/children`, with `position.after_block` when `after` is given.
- `data_source(token, database)`: the wishlist data source id and property schema.
- `create_page(token, data_source_id, properties, children)`.

Every write goes through one `Engine._write(fn)` wrapper. On HTTP 403 it sets a `writes_disabled` flag in the store cache, sends the one-time "add Insert content" message, and returns. Every pass checks the flag and skips writes cheaply. The flag expires after 6 hours, so granting the permission is picked up on its own. *Alternative*: checking the integration's capabilities up front. The API doesn't expose them, so attempting and catching the 403 is the only reliable test.

### 2. Placing a line in its section
`thesis.py` already maps sections to lines. It now also keeps the **block id of each section's last block**, and of its heading when the section is empty. Appending a line means `append_blocks(page, [line], after=last_block_id)`. A missing section means appending `[heading_2, line]` at the end of the page. Headings are matched the same fuzzy way as when reading, so "REVIEW LOG (one line per quarter)" is found and a second heading is never created. A block id goes stale if the investor deletes that block between the read and the write. To cover that, the page is re-read immediately before each write, and a 404/400 on the anchor triggers one re-read and retry.

### 3. State the bot keeps (SQLite)
| Table | Columns | Purpose |
|---|---|---|
| `stock_reviews` | symbol, market, quarter, reviewed_at, written | one review per stock per quarter; `written=0` when Notion refused, so it is retried while that quarter is still current |
| `verdict_state` | symbol, market, list_type, blueprint_version, verdict, results_json, updated_at | last verdict and per-rule/breaker statuses, for change detection |
| `digest_pending` | symbol, market, line, created_at | price-only changes waiting for the next digest; cleared when sent |

Entry snapshots reuse `seen_items` with scope `entry:portfolio`. The first sync after deploy marks every current portfolio stock as seen without writing, which is the `new_items` baseline pattern. A stock that later appears in the portfolio, including one moved from the wishlist, is unseen and gets a snapshot.

### 4. Where each pass runs
```
Engine.check() (every 15 min)
  sync -> entry snapshots for unseen portfolio stocks
  monitor.run()            (existing alerts)
  reviewer.run()           new quarter? -> evaluate + track + model evidence
                           -> review line (write) -> verdict compare -> alert
digest_job (08:30 IST)
  conviction.daily()       evaluate all (no model) -> verdict compare
                           -> business changes: alert now
                           -> price-only: digest_pending
                           -> action-plan + weight triggers
  digest.build(..., conviction_lines=...)   adds the "Conviction" section
/blueprint lock
  reevaluate_all(baseline=True) -> one summary message; verdict_state overwritten
```
The reviewer runs inside `Monitor.lock`'s critical section (after `monitor.run`), so a manual `/checknow` and the scheduled job can't review the same stock twice.

### 5. Change classification
Comparing new results with `results_json`, keyed by rule text and breaker text:
- Stored blueprint version ≠ current, a list change (wishlist ↔ portfolio), or no stored row: **baseline**. Overwrite and stay silent.
- Verdict unchanged: overwrite, silent.
- Verdict changed, and any non-Valuation rule or any measurable breaker changed status: **business**, alert now.
- Verdict changed, and only Valuation rules changed: **price-only**, goes to `digest_pending`.

Possible (model-read) breakers never take part, because they never move the verdict.

### 6. Thesis tracking parsing
In `thesis.py`:
- `track:` regex: `track:\s*(?P<metric>.+?)\s*(?P<op>>=|<=|≥|≤|>|<|above|below)\s*(?P<num>-?[\d,]*\.?\d+)\s*%?`. The metric goes through `blueprint.metric_key()`, and judgment or unknown means unmeasurable. `>` and `<` are strict; `above` and `below` map to `>` and `<`.
- Action plan: `add below\s*[₹$]?\s*(num)`, `trim above\s*[₹$]?\s*(num)(?!\s*%)`, `(?:grows past|above)\s*(num)\s*%\s*of (?:my )?portfolio`. Placeholders such as `₹/$ X` don't match.

`decide()` gains a `breakers_hit` argument and inserts the REVIEW step right after SELL, per the modified `conviction-verdict` spec.

### 7. Model-read evidence
One structured call per stock per review (and per `/why` that has unmeasurable lines), using a new `TRACKING_SYSTEM` prompt. Input: the numbered unmeasurable lines (each labelled pillar or breaker), plus results filings and news since the last review (`/why`: the last 90 days), from `report.updates()`. Output schema: `[{index, status: supports|challenges|no_evidence, source, quote ≤ 25 words}]`. For a breaker, `challenges` means "possibly hit". The prompt tells the model that "no evidence" is the safe default. Model unavailable means "not checked". *Alternative*: having the model decide breakers outright. Rejected: a 7B model reading headlines is not reliable enough to move a verdict, and "code decides" was agreed.

### 8. Weight and FX
`market.fetch_fx("USDINR=X")` is cached for 6 hours. The weight uses the quotes `digest.build()` already fetches, so there is no second quote round-trip. Holdings without a quantity are excluded from both the numerator and the denominator.

### 9. Net cash
`INFO_FIELDS` gains `totalCash → cash` and `totalDebt → total_debt_info`. `metrics.compute()` sets `net_cash = cash − total_debt`, preferring the latest annual `total_debt` when the info value is missing. The metric registry gets unit `money` (formatted with `fmt.money` in the statement currency) and `not_for_financials=True`.

### 10. Commands
- `/log`: regex `^(\S+)\s+(BUY|ADD|TRIM|SELL)\s+([\d.]+)\s*@\s*[₹$]?([\d.,]+)\s*(.*)$` (case-insensitive). The ticker goes through `pick()`, and the stock must have a page. CLI: `log …`.
- Start thesis: a callback `st|SYMBOL|MARKET` on `/why` when the stock has no page. Before creating anything, the bot re-syncs and re-checks for a page, which makes a double tap harmless. It builds properties from the detected wishlist columns and the data source's property types (title, rich_text, select), and children from `templates/thesis.md` converted to blocks. The BLUEPRINT CHECKS section is generated from the locked blueprint. Afterwards it syncs and replies with the page URL.

## Risks / Trade-offs

- [Positional insert behaves differently at Notion-Version 2025-09-03 than documented] → Task 1.1 is a live check first. Fallback: append at page end, a deliberate spec change made through `/opsx:update` before going further.
- [The bot writes a line while the investor edits the same page] → Inserts don't conflict with edits. At worst the line lands just after a block the investor moved, which is still inside the page and visible.
- [P/E hovering at a rule threshold flips HOLD ↔ ADD daily] → Price-only changes only reach the digest, never a push. Hysteresis is a non-goal for now, revisit if noisy.
- [Model false "possibly hit"] → Never changes the verdict; shown with source and quote so the investor can judge it in seconds.
- [First run writes a review line for every holding with a page] → Intended: it gives each thesis a starting entry. It is one line per holding, once.
- [Yahoo adds quarters weeks after results] → Review lines record the quarter and the date of review, so the lag is visible. The results-filing alert still arrives the same day through the existing monitor.
- [A Notion 429 during a burst of reviews] → Reuses the 429 retry from `conviction-blueprint`. Reviews are spread naturally, since quarters land on different days.
- [Creating wishlist rows needs the right property types] → Built from the data source schema, not guessed. An unsupported type (e.g. a relation) is left empty and reported in the reply.

## Migration Plan

1. Prerequisite: `conviction-blueprint` is implemented and archived.
2. Deploy: the new tables are created on start-up. The first sync records the entry baseline, the first monitor pass writes one review per holding with a page, and the first evaluation records the verdict baseline with no alerts.
3. The investor enables *Insert content* on the Notion integration (Settings → Connections → the integration → Capabilities). Until then, writes stay off after one message.
4. Rollback: disable the reviewer and the daily pass. Lines already written stay in Notion as ordinary text, and the tables are ignored by older code.
