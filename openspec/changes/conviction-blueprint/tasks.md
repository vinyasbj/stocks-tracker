# Tasks

## 1. Data foundations

- [x] 1.1 Add `ni_cagr` / `ni_cagr_years`, `op_margin` (Yahoo TTM operating margin, falling back to the latest quarter's) and `loss_years` (consecutive fiscal years with a net loss, counting back from the latest; `None` without annual rows) to `metrics.compute()`; verify with new cases in `tests/test_analysis.py` (CAGR from annual net income, op-margin fallback, loss streak of 2 / 1 / 0 / no annual data)
- [x] 1.2 Generalise `notion.pick_columns()` into `find_columns(properties, candidates, overrides)`, keeping `pick_columns` behaviour, and add `page_id` and an optional `conviction` column to `NotionRow`; verify the existing `tests/test_portfolio.py` parsing tests pass unchanged and a new test reads page id and conviction from the fixture
- [x] 1.3 Add `notion.page_blocks(token, page_id)`, which paginates `GET /blocks/{id}/children` and recurses into nested headings, toggles, list items and columns up to depth 3, plus a retry on HTTP 429 that honours `Retry-After` in the Notion client; verify with `httpx.MockTransport` tests for pagination, nesting order, the 429 retry, and a 404 raising the "add your integration" error
- [x] 1.4 Add `pages` and `conviction` to `Holding`; add `pages` and `conviction` columns to `holdings` with an `ALTER TABLE` migration; merge page ids of portfolio lots in `replace_holdings()` and keep none from wishlist rows; pass page id and conviction through `sync.build_holdings()`; verify with tests for merged lots (both page ids kept, Notion order), a wishlist row contributing no page id, and opening a database created with the old schema
- [x] 1.5 Add `longBusinessSummary → business_summary` and `country` to `market.INFO_FIELDS`, kept as text; verify a test that `fetch_fundamentals` keeps both strings unconverted
- [x] 1.6 Treat `NOTION_WISHLIST_DB` as unset unless it holds a Notion id, logging once; verify a config test for empty and `https://www.notion.so/...` values and an engine sync test with only the portfolio set that succeeds

## 2. Blueprint rules and locking

- [x] 2.1 Create `analysis/blueprint.py` with the metric registry (labels, units, aliases, `not_for_financials`; including Consecutive loss years), `Rule`, and `parse_rules(pages) -> (rules, pass_bar, problems)`, covering judgment rules, the Settings "Scored rules pass bar" row (default 75), text-typed columns, and the problem cases (unknown metric, missing threshold, unclear condition, fraction in a percentage rule); verify against a new `tests/fixtures/notion_blueprint.json` in a new `tests/test_blueprint.py`
- [x] 2.2 Add `sort_rules`, `fingerprint` (order-independent) and `diff` (added / removed / changed by normalised text); verify that reordered rows give the same fingerprint and that a threshold edit shows as "changed"
- [x] 2.3 Add the `blueprint_versions` table and `Store.lock_blueprint()` / `Store.locked_blueprint()`, storing rules and pass bar as JSON; verify that versions increment, the latest is returned, and earlier versions remain
- [x] 2.4 Add `templates/blueprint.csv` with the starter rules from design.md; verify a test that builds text-typed Notion rows from the CSV and parses them with zero problems into 15 rules and a 75% bar, including the must-pass rule Consecutive loss years ≤ 1 and the scored rule Profit growth YoY ≥ 0

## 3. Thesis reading

- [x] 3.1 Create `analysis/thesis.py`: blocks → `Thesis` (sections, to-dos with ticked state, conviction, started date), recognising heading blocks and plain-paragraph headings, with parenthesised text ignored; verify against a new `tests/fixtures/notion_thesis_blocks.json` covering heading blocks, an uppercase paragraph heading, nested bullets under a toggle, and the header line
- [x] 3.2 Add judgment answer matching (ticked / unticked / missing; best of `ratio` and `token_sort_ratio` ≥ 85 on normalised text); verify ticked, unticked, slightly reworded, unrelated, and short-subset to-do cases
- [x] 3.3 Add `templates/thesis.md` (the investor's template, `##` headings, BLUEPRINT CHECKS tick boxes); verify a test that its tick-box lines match the starter CSV's judgment rules exactly

## 4. Evaluation and verdicts

- [x] 4.1 Create `analysis/verdict.py` with `Check` and `evaluate(rules, metrics, fundamentals, todos)`; verify one test per scenario in `specs/conviction-verdict` "Each rule is checked…" (pass with value, no data, bank not applicable, stale quarter, negative earnings P/E, loss-making two years / one year / no annual results, judgment)
- [x] 4.2 Add the scored share and `decide(checks, held, pass_bar)` in the specified order for not-held (BUY / WATCH / AVOID) and held (ADD / HOLD / REVIEW / SELL) stocks, with reasons naming up to three rules; verify a table-driven test covering every verdict scenario in the spec, including "unknown never causes SELL or REVIEW"
- [x] 4.3 Add the conviction mismatch rule (HIGH with SELL / REVIEW / AVOID; LOW with ADD / BUY); verify with tests

## 5. Engine, prompts and rendering

- [x] 5.1 Add `NOTION_BLUEPRINT_DB` to `config.py` and `.env.example`; verify that `load_settings()` reads it in a test and that an empty value leaves the blueprint commands explaining the setup
- [x] 5.2 Add `Engine.blueprint_status()` (locked version, Notion rules, problems, diff, Notion error) and `Engine.lock_blueprint()`; verify with a monkeypatched Notion fetch: first lock gives v1, unchanged gives no new version, edit gives v2 with diff, invalid rows refuse, Notion down still returns the locked version
- [x] 5.3 Add `Engine.conviction(sec)`: fundamentals, metrics, thesis from the portfolio holding's pages (first non-empty), evaluate, decide; and `Engine.conviction_report()` over portfolio holdings only, with a semaphore of 4 and per-stock error capture; verify with stubbed fundamentals and Notion that an unreadable page gives a verdict with unconfirmed judgments and a note, and that one failing stock doesn't stop the report
- [x] 5.4 Split the prompt grounding so only `CONVICTION_SYSTEM` omits "do not tell the user to buy or sell"; add `CONVICTION_SYSTEM` and the `check` intent to `INTENT_SYSTEM` and `Intent`; verify a test that every other system prompt still contains the no-buy/sell sentence
- [x] 5.5 Create `analysis/conviction.py` renderers for `/why` (header, verdict, business profile, version, rules by pillar, thesis or "no thesis yet", summary placeholder, reworded disclaimer; made compact in 8.2), `/report` (summary counts, verdict groups SELL, REVIEW, HOLD, ADD, no-thesis and mismatch lists; tables in 8.3) and `/blueprint` (rules by pillar, pass bar, unlocked-edit notice, tick-box block), plus `split_messages()`; verify with snapshot-style tests, including a 40-holding report split into several messages that together list every holding, and the profit part for a profitable and a loss-making company
- [x] 5.6 Add the `PROFILE_SYSTEM` prompt and `Engine.business_profile()`: at most 3 lines from the description, the number guard falling back to the first ~300 characters of the description, a 30-day cache keyed by the description hash, and a no-description note; verify with a stubbed LLM that an invented number triggers the fallback, a second call hits the cache, the model being down gives the fallback, and the text states that no segment split is available

## 6. Telegram and CLI

- [x] 6.1 Add `/why` (action `ck`, through `pick()` and buttons), `/report` and `/blueprint [lock]` handlers, route the `check` intent in `on_text`, and update `COMMANDS` and `HELP`; `/why` sends the code-rendered message first and then edits in the business profile and the summary; verify by running the bot and exercising each command in Telegram, including an ambiguous name such as "infosys"
- [x] 6.2 Add CLI commands `why`, `report`, `blueprint [lock]`; verify that `PYTHONPATH=src venv/bin/python -m stockbot.cli blueprint`, `… why TCS` and `… report` print the expected plain text
- [x] 6.3 Update the README: blueprint setup (CSV import, plain number format, sharing, `NOTION_BLUEPRINT_DB`, the optional Notion "Lock database"), the thesis template, the new commands, the verdict meanings, the business profile and its limits (no segment split), the profit-making rule, that the wishlist is not used by convictions, and the reworded "not advice" line; verify that every README command runs as written

## 7. Integration check

- [x] 7.1 Run `venv/bin/python -m pytest` and verify that all tests, old and new, pass
- [x] 7.2 End-to-end with the real Notion workspace and **no wishlist configured**: import the CSV, lock v1, `/why` a stock you don't own (BUY / WATCH / AVOID, with its business profile and profit figures), `/why` a company loss-making in each of its last two years (AVOID), `/why` a holding with a filled thesis (ticks respected), `/report`, then edit a threshold in Notion and verify the unlocked-edit notice, and `/blueprint lock` producing v2 with the diff

## 8. Review follow-ups

- [x] 8.1 Make `/why` the only check command (no `/check`; the CLI takes `why`), across the bot, CLI, help, README and both changes' artifacts; verify the tests and that `/check` is no longer registered
- [x] 8.2 Compact `/why`: failures first, passing rules folded per pillar, unconfirmed judgment rules on one line, "Your conviction: not set" for a holding without one, no segment-split line, a summary capped at three sentences that doesn't repeat the verdict or mention a missing thesis, and a long reply that shrinks the thesis and then cuts the middle, never the summary or disclaimer; verify with renderer tests and a run against real data
- [x] 8.3 `/report` as aligned monospace tables per verdict, with each rule's limit stated once; re-read Notion before reporting (say so when it can't); ETFs and funds listed as not judged; an unreadable thesis page kept apart from "no thesis"; verify with renderer tests and a run against real data
- [x] 8.4 Match a holding by symbol and market only, so a differently spelt company name still finds it; verify with a test
- [x] 8.5 Report `>`, `<`, "above" and "below" conditions and unknown pillars instead of guessing; verify with tests
- [x] 8.6 For a stock not held, judgment rules are not applicable and a BUY says how many to confirm before buying; verify with tests
- [x] 8.7 Show a net margin of n/a when Yahoo reports 0 for a company with no revenue, and say changes in a loss in words ("loss widened 97%"); verify with tests
- [x] 8.8 ETFs and funds in `/why`: found by exact ticker (preferred over fuzzy company-name matches, never over an exact listed symbol), shown with Yahoo's fund figures, description and the thesis, with no verdict and no model call; the header leaves out a missing market cap and sector; verify with tests and real runs for QQQM, URA, SOXX and DRAM
- [ ] 8.9 Re-run 7.2 in Telegram after these changes, with only one bot instance polling the token
