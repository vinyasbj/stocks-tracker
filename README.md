# stock-bot

A personal Telegram bot for Indian (NSE) and US stocks. It runs on your laptop with a free local LLM.

- **`/analyze TICKER`**: strengths and weaknesses, key ratios, the latest quarter versus the previous quarter and the same quarter last year, and a short summary.
- **`/results TICKER`**: the last five quarters in a table, the result filings, and an explanation.
- **`/news TICKER`**: the latest exchange or SEC filings and news, each tagged 🟢 positive, 🔴 negative or ⚪ neutral.
- **Alerts for your Notion portfolio 💼 and wishlist ⭐ only**: every 15 minutes the bot re-reads both Notion databases and checks those stocks for new filings, important news, and SEBI, RBI or SEC releases that affect them. To start or stop tracking a stock, add or remove it in Notion.
- **`/portfolio`**: each holding's value, day change and unrealised P&L, with totals per currency (INR and USD, not converted), plus wishlist prices and distance from the 52-week high. The same view arrives with headlines as a digest each morning at 08:30 IST (**`/digest`**).
- **`/why TICKER`**: any listed stock judged by **your own conviction blueprint**: a verdict, every rule's result, how and where the company makes its money with its profit figures, and your written thesis. See [Conviction blueprint](#conviction-blueprint).
- **`/report`**: every stock in your Notion portfolio judged by your blueprint, most urgent first.
- **`/sync`**: re-reads Notion now and reports what was added or removed, and any rows the bot can't match to a listed stock.
- `/analyze`, `/results`, `/news` and free-text questions work for **any** listed stock, so you can research a stock before adding it to the wishlist.
- **Free text**: ask things like *"how were Infosys results?"* or *"is AAPL's debt a concern?"*. Follow-up questions about the same stock work.

## How it works

Code fetches the data, computes the ratios and applies fixed rules to find strengths and weaknesses. The local model (Ollama, `qwen2.5:7b` by default) only writes the prose, tags news by impact, decides whether a regulator release matters, and works out which stock a free-text question is about. Every number comes from the data, not from the model.

| Data | India | US |
|---|---|---|
| Prices, ratios, statements | Yahoo Finance (`yfinance`) | Yahoo Finance |
| Filings | NSE corporate announcements | SEC EDGAR (8-K, 10-Q, 10-K) |
| News | Google News RSS, Yahoo news | same |
| Regulators | SEBI, RBI (press releases and notifications) | SEC press releases |

## Setup

```sh
python3 -m venv venv && venv/bin/python -m pip install -r requirements.txt
ollama pull qwen2.5:7b        # skip if you already have it
cp .env.example .env
```

1. In Telegram, message **@BotFather**, send `/newbot`, and put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Set `SEC_USER_AGENT` to `stock-bot <your email>`. SEC requires a contact.
3. Connect Notion (read-only):
   - Go to notion.so/profile/integrations, create a **New integration** (internal; *Read content* is enough), and put its token in `NOTION_TOKEN`.
   - In both the portfolio and the wishlist database, open **⋯ → Connections** and add the integration.
   - Paste the two database links into `NOTION_PORTFOLIO_DB` and `NOTION_WISHLIST_DB`.
   - The bot finds the usual column names: *Ticker/Symbol*, *Name/Company* (or the title column), *Exchange/Market* (NSE, BSE, NYSE, NASDAQ…), *Qty/Quantity/Shares*, and *Avg price/Buy price/Avg cost*. If yours differ, set `NOTION_COL_*` in `.env`.
   - Check the result with `PYTHONPATH=src venv/bin/python -m stockbot.cli sync`.
4. Run `./run.sh`, send `/start` to your bot, copy the chat ID it replies with into `ALLOWED_CHAT_IDS`, and restart. The bot ignores every chat not on that list.

To start the bot when you log in, see [deploy/com.stockbot.plist](deploy/com.stockbot.plist). Alerts only arrive while the laptop is awake.

## Conviction blueprint

Your blueprint is your own fixed set of investing rules. Every stock is judged by the same rules, so you can always say why you hold a stock and when you would sell it.

### Set it up once

1. In Notion, import [templates/blueprint.csv](templates/blueprint.csv) as a new database (**Import → CSV**). It has 15 starter rules; edit them to match how you invest. Each row has these columns:
   - **Rule**: the rule in your words.
   - **Pillar**: Business, Quality, Growth, Safety or Valuation (any other value is reported and the row skipped).
   - **Metric**: one of ROE, Net margin, Operating margin, Debt/Equity, Current ratio, P/E, Forward P/E, P/B, Revenue growth YoY, Profit growth YoY, Revenue CAGR 3Y, Profit CAGR 3Y, FCF positive years, Consecutive loss years, Dividend yield or From 52w high. Leave it empty (or write *Judgment*) for a rule you answer yourself, such as *"I understand how the company makes money"*.
   - **Condition** (≥ or ≤; `>`, `<`, *above* and *below* are reported, not guessed) and **Threshold**: write plain numbers, so 15 means 15%. Don't use Notion's percent format, which stores 0.15.
   - **Must pass**: Yes for a deal-breaker.
   - The **Settings** row *"Scored rules pass bar"* (75 to start) is the share of the other rules a stock must pass.
2. Share the database with your integration (**⋯ → Connections**) and put its link in `.env` as `NOTION_BLUEPRINT_DB`. Restart the bot.
3. Send **`/blueprint`** to check what the bot read, then **`/blueprint lock`**. That fixes it as **v1**.

Editing the Notion database later changes nothing until you send `/blueprint lock` again. The bot then shows what changed and stores v2, and every verdict names the version that made it. You can also turn on Notion's own **⋯ → Lock database** so nobody edits it by accident.

The starter blueprint includes the rule *"Profit-making: not loss-making in each of the last 2 years"* (Consecutive loss years ≤ 1, must pass). A company that lost money in both of its last two fiscal years is AVOID if you don't own it and SELL if you do.

### Write a thesis for each holding

Open the stock's row in your Notion **portfolio** and paste [templates/thesis.md](templates/thesis.md) into the page. It has your one-line thesis, why you own it, entry, risks, thesis breakers, action plan, review log and decision log. It ends with **Blueprint checks**: one tick box per judgment rule. Tick only what is true for that stock. `/blueprint` prints the current tick-box block to paste. Put `Conviction: HIGH`, `MEDIUM` or `LOW` on the first line, or in a *Conviction* column.

The wishlist is not used for convictions. A stock you don't own has no thesis, and `/why` still judges it.

### Verdicts

Code decides the verdict from your rules; the local model only explains it.

| | Stock you don't own | Stock in your portfolio |
|---|---|---|
| A must-pass rule fails | **AVOID** | **SELL** |
| Fewer than 75% of the other rules pass | **AVOID** | **REVIEW** |
| A must-pass rule can't be confirmed (an unticked box, or no data) | **WATCH** (no data only) | **HOLD**, "confirm …" |
| A Valuation rule fails (good business, wrong price) | **WATCH** | **HOLD**, "too expensive to add" |
| Everything passes | **BUY**, "before buying, confirm N judgment rules" | **ADD** |

Judgment rules (the tick boxes) only count for stocks you own: a stock you don't own has no thesis to tick, so it is judged on the numbers, and a BUY reminds you to confirm the judgment rules yourself before buying. Missing data never causes SELL or REVIEW. Debt/Equity, Current ratio and FCF years don't apply to banks and other financials. If your conviction level disagrees with the verdict (HIGH but SELL, or LOW but ADD), the bot points it out.

`/why` also shows **how and where the company makes money**, summarised from Yahoo's company description, and its **profit figures**: the latest quarter's net profit with YoY and QoQ growth, the latest full year, 3-year profit CAGR, and net and operating margin. A change in a loss is said in words ("loss widened 97%", "swung to a loss"). Yahoo has no split of profit by segment or country, so the bot never estimates one.

## Try it without Telegram

```sh
PYTHONPATH=src venv/bin/python -m stockbot.cli analyze RELIANCE
PYTHONPATH=src venv/bin/python -m stockbot.cli results AAPL
PYTHONPATH=src venv/bin/python -m stockbot.cli news "hdfc bank"
PYTHONPATH=src venv/bin/python -m stockbot.cli ask "how were infosys results?"
PYTHONPATH=src venv/bin/python -m stockbot.cli sync         # what the bot reads from Notion
PYTHONPATH=src venv/bin/python -m stockbot.cli portfolio    # P&L
PYTHONPATH=src venv/bin/python -m stockbot.cli monitor      # one alert pass (the first only records a baseline)
PYTHONPATH=src venv/bin/python -m stockbot.cli blueprint    # your blueprint; add "lock" to lock it
PYTHONPATH=src venv/bin/python -m stockbot.cli why TCS      # TCS judged by your blueprint
PYTHONPATH=src venv/bin/python -m stockbot.cli report       # your portfolio judged by your blueprint
venv/bin/python -m pytest
```

## Tickers

Use an NSE symbol (`RELIANCE`, `INFY.NS`), a US ticker (`AAPL`, `BRK.B`), or a company name. When a name matches more than one stock, for example an NSE company and its US ADR, the bot shows buttons so you can pick one. You can also add a prefix: `NSE:infosys`, `US:infy`.

ETFs and funds (`QQQM`, `SOXX`, `NIFTYBEES.NS`) aren't in the exchange lists, so type their exact ticker. `/why` shows a fund's figures (assets, expense ratio, yield, returns, 52-week range), what it holds and your thesis, but gives no verdict: your blueprint's rules are for companies. `/report` lists funds as not judged.

## Notion matching rules

- A ticker must match a listed symbol exactly: `RELIANCE`, `RELIANCE.NS`, `NSE:RELIANCE` or `AAPL`. A company name must match closely. The bot never guesses: rows it can't place are reported once in Telegram and every time you run `/sync`.
- When a ticker is listed on both NSE and a US exchange under the same name (for example `INFY`), set the Exchange column, or write `INFY.NS`.
- Several rows for the same stock (separate lots) are added together, with a weighted average price. A stock in both lists counts as portfolio.
- Average prices are read in the stock's trading currency: ₹ for NSE, $ for US.
- If Notion can't be reached, the bot keeps tracking the lists from the last successful sync.

## Known limits

- Yahoo sometimes stops updating a company's quarterly statements (HDFC Bank's stop at Jun 2025). The bot detects this, says so, and leaves those quarters out of the strengths and weaknesses rules. Use `/news` for the latest results.
- BSE's API blocks scripted clients, so Indian filings come from NSE only.
- On a 16 GB Mac, `qwen2.5:7b` takes about 10–30 s per reply. To try another model, set `OLLAMA_MODEL`, for example to `qwen3:8b` or `llama3.1`.
- The bot does not give investment advice. `/why` and `/report` verdicts apply the rules **you** wrote in your blueprint; they are not the bot's recommendation.
