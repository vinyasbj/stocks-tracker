# stock-bot

A personal Telegram bot for Indian (NSE) and US stocks. It runs on your laptop with a free local LLM.

- **`/analyze TICKER`**: strengths and weaknesses, key ratios, the latest quarter versus the previous quarter and the same quarter last year, and a short summary.
- **`/results TICKER`**: the last five quarters in a table, the result filings, and an explanation.
- **`/news TICKER`**: the latest exchange or SEC filings and news, each tagged 🟢 positive, 🔴 negative or ⚪ neutral.
- **Alerts for your Notion portfolio 💼 and wishlist ⭐ only**: every 15 minutes the bot re-reads both Notion databases and checks those stocks for new filings, important news, and SEBI, RBI or SEC releases that affect them. To start or stop tracking a stock, add or remove it in Notion.
- **`/portfolio`**: each holding's value, day change and unrealised P&L, with totals per currency (INR and USD, not converted), plus wishlist prices and distance from the 52-week high. The same view arrives with headlines as a digest each morning at 08:30 IST (**`/digest`**).
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

## Try it without Telegram

```sh
PYTHONPATH=src venv/bin/python -m stockbot.cli analyze RELIANCE
PYTHONPATH=src venv/bin/python -m stockbot.cli results AAPL
PYTHONPATH=src venv/bin/python -m stockbot.cli news "hdfc bank"
PYTHONPATH=src venv/bin/python -m stockbot.cli ask "how were infosys results?"
PYTHONPATH=src venv/bin/python -m stockbot.cli sync         # what the bot reads from Notion
PYTHONPATH=src venv/bin/python -m stockbot.cli portfolio    # P&L
PYTHONPATH=src venv/bin/python -m stockbot.cli monitor      # one alert pass (the first only records a baseline)
venv/bin/python -m pytest
```

## Tickers

Use an NSE symbol (`RELIANCE`, `INFY.NS`), a US ticker (`AAPL`, `BRK.B`), or a company name. When a name matches more than one stock, for example an NSE company and its US ADR, the bot shows buttons so you can pick one. You can also add a prefix: `NSE:infosys`, `US:infy`.

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
- The bot does not give investment advice.
