# Tasks

## 1. Spike and Notion writing

- [ ] 1.1 Spike against the real workspace, with a scratch page, at Notion-Version `2025-09-03`: append a paragraph with `position.after_block` between two existing blocks, check that a read-only integration gets 403, and create a row in a data source; verify by reading the page back in the expected order and recording the outcome in design.md (if positional insert fails, stop and revise the spec through `/opsx:update` before continuing)
- [ ] 1.2 Add `notion.append_blocks(parent, blocks, after=None)`, `notion.data_source()` and `notion.create_page()`, plus block builders for paragraph, heading_2 and to_do; verify with `httpx.MockTransport` tests asserting the request bodies (`position` shape, at most 100 children) and that 403 raises a distinct permission error
- [ ] 1.3 Add the `Engine._write()` wrapper: on 403, set `writes_disabled` (6 h), send the "add Insert content" message once (a `writes_warned` flag cleared after a successful write), and skip writes while the flag is set; verify with tests in which a refused write sends exactly one message over two passes and reads keep working

## 2. Section anchors and the journal

- [ ] 2.1 Extend `thesis.py` to keep each section's heading block id and last block id; add `append_to_section(page, section, line)`, which re-reads the page, inserts after the last block (or adds heading plus line at the end when the section is missing), and on a stale anchor re-reads once and retries; verify with fixture-based tests for existing section, empty section, missing section, an uppercase paragraph heading, and a stale-anchor retry
- [ ] 2.2 Mark bot-composed lines with "🤖"; verify in the review-line and entry-line renderer tests
- [ ] 2.3 Entry snapshots: after each sync, write one ENTRY line for portfolio stocks not yet in `seen_items` scope `entry:portfolio`, with the first ever pass recording a baseline only; verify tests for deploy-with-12-holdings (no writes), a new holding (one write), wishlist → portfolio (one write), and a second sync (no write)
- [ ] 2.4 `/log` command and CLI `log`: parse, resolve via `pick()`, require a page, and append to DECISION LOG with the date and currency symbol; verify parser tests (valid, lowercase action, ₹ and $ prices, wrong form) and a handler test that the wrong form writes nothing and replies with usage
- [ ] 2.5 Start-thesis button (`st|SYMBOL|MARKET`) on `/why` for stocks without a page: re-sync and re-check, then build properties from the wishlist data source schema and children from `templates/thesis.md` plus the locked blueprint's tick boxes, create the page, sync, and reply with the link; verify with a mocked-Notion test that a double tap creates one row, and a manual run in Telegram
- [ ] 2.6 Update the README: enabling *Insert content*, what the bot writes and where, the 🤖 mark, `/log` syntax, and the Start-thesis button; verify that the README `/log` example parses with the task 2.4 parser

## 3. Thesis tracking

- [ ] 3.1 Parse `track:` conditions on pillars and breakers via `blueprint.metric_key()` (strict `>` / `<`, `above` / `below`, optional `%`); verify with tests from the spec scenarios (measurable P/E pillar, segment metric unmeasurable, "rising" unmeasurable)
- [ ] 3.2 Evaluate pillars (met / not met / no data) and breakers (hit / clear / no data); verify with unit tests
- [ ] 3.3 Add the Net cash metric (`totalCash`, `totalDebt` in `INFO_FIELDS`; `net_cash` in `metrics.compute()`; registry unit `money`, not for financials); verify a metrics test and blueprint-rule tests for the cash-rich and bank scenarios
- [ ] 3.4 Add `breakers_hit` to `decide()` with REVIEW right after SELL; verify the new scenarios in the modified `conviction-verdict` spec (breaker alone → REVIEW, breaker plus must-fail → SELL, possible-only → unchanged)
- [ ] 3.5 Add the `TRACKING_SYSTEM` prompt and structured schema; gather results filings and news since the last review (the last 90 days for `/why`); breakers with `challenges` are shown as possible; model unavailable gives "not checked"; verify with a stubbed LLM that statuses map correctly and the verdict is untouched
- [ ] 3.6 Parse action-plan triggers (add below, trim above price, trim above weight %; placeholders ignored); verify with tests including "Add below ₹/$ X"
- [ ] 3.7 Show tracking in `/why`: pillar statuses, breaker statuses (hit / clear / possible / no data), and action-plan levels beside the current price or weight; verify with a renderer snapshot test

## 4. Reviews, verdict history and alerts

- [ ] 4.1 Add the `stock_reviews`, `verdict_state` and `digest_pending` tables with store methods; verify store tests for the unique (stock, quarter) review and verdict upsert
- [ ] 4.2 Add the reviewer pass inside the monitor lock after `monitor.run()`: detect a new non-stale quarter, evaluate, track, gather model evidence, write the review line (marking `written=0` on refusal and retrying while the quarter is current), and record the verdict; verify tests for a new quarter (one line), restart (none), stale (none), a holding without a page (no write, verdict still recorded), and refused-then-granted
- [ ] 4.3 Add change classification (baseline / unchanged / business / price-only) over `results_json`; verify table-driven tests for each class, including blueprint-version change and list change as baseline
- [ ] 4.4 Business-change alert renderer and sending: old → new, changed rules and breakers with old and new values, blueprint version; verify a snapshot test of the TCS HOLD → SELL example and that a repeat evaluation sends nothing
- [ ] 4.5 Daily pass before the digest: evaluate all (no model), alert business changes, queue price-only changes, compute action-plan and weight triggers with `fetch_fx("USDINR=X")`, and add the "Conviction" digest section (omitted when empty, with a note when FX is unavailable); verify tests for the GOOGL P/E crossing (digest only), the add-below hit, the 17.4% weight example, FX failure, and an empty section
- [ ] 4.6 After `/blueprint lock`: re-evaluate all with baseline and send one summary of changed verdicts, with no individual alerts; verify a test where v2 moves three holdings and exactly one message is sent
- [ ] 4.7 Document reviews, alerts and the digest section in the README; verify the README describes each alert type with an example matching the renderer output

## 5. Integration check

- [ ] 5.1 Run `venv/bin/python -m pytest` and verify that all tests pass
- [ ] 5.2 End-to-end on the real workspace with *Insert content* enabled: `/log` a decision, Start a thesis for a new stock, add a stock to the portfolio (entry line appears), force a review by clearing that stock's `stock_reviews` row (a line under REVIEW LOG), set a breaker `track: P/E > 1` (REVIEW and an alert), and run `/digest` with an add-below price above the current price (Conviction section); then remove *Insert content* and verify one warning while `/why` keeps working
