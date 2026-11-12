# stock-bot

A personal Telegram bot for Indian (NSE) and US stocks, with a free local LLM. It runs on an Oracle Cloud VM beside the expense-tracker bot (see [Run on the server](#run-on-the-server)), or on your laptop.

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

## Setup (run locally)

```sh
python3 -m venv venv && venv/bin/python -m pip install -r requirements-dev.txt
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

**While the server bot runs, don't run `./run.sh` or the launchd agent with the same token.** Two processes polling one bot token make Telegram return 409 Conflict, and both miss messages. To try changes locally, create a second test bot with @BotFather and put its token in your local `.env`. The `stockbot.cli` commands (see [Try it without Telegram](#try-it-without-telegram)) don't poll Telegram, so they are always safe to run.

## Run on the server

The bot runs in Docker on the Oracle Cloud VM, next to the expense-tracker stack, and uses that stack's Ollama. A push to `main` that passes the tests deploys itself.

```
 Oracle VM (Ampere A1)
 +------------------------------------------------------------------+
 |  ~/expense-tracker                   ~/stock-bot (this repo)     |
 |  expense-bot                         stock-bot                   |
 |  expense-ollama  <-- expense-tracker_expense_net --+             |
 |   (qwen2.5:7b, one copy for both bots)             |             |
 |  expense-logs (Dozzle: shows both)   volume:                     |
 |                                      stock-bot_stockbot_data     |
 +------------------------------------------------------------------+
```

### What it depends on

- **The expense-tracker stack** running from `~/expense-tracker`, with `qwen2.5:7b` pulled (its `ollama-init` does that). Both bots send `qwen2.5:7b` with context 8192, so Ollama keeps one copy in memory. Changing `OLLAMA_MODEL` or `OLLAMA_NUM_CTX` here would load a second copy.
- **Its Docker network** `expense-tracker_expense_net`. After any `docker compose down` in `~/expense-tracker`, run `docker compose up -d` in `~/stock-bot` to reattach.

### First install (once)

1. **Clone** the repo to `~/stock-bot` the same way `~/expense-tracker` was cloned (`git -C ~/expense-tracker remote -v` shows how).
2. **Measure the model** on the server: run `docker exec -it expense-ollama ollama run qwen2.5:7b --verbose` and ask something short to warm it up, then paste about 1,000 tokens of text (a `/why` prompt is 800–1,100). The *prompt eval rate* and *eval rate* lines give tokens per second; on the 4-core VM they were about 20 and 6. A `/why` there takes 28–53 s (the model reads the prompt at about 20 tokens/s, so the bot sends a short one and caps the reply), so `OLLAMA_TIMEOUT=90`. If your measurement differs, use the slowest `/why` time plus 50%.
3. **Settings**: `cp .env.example .env`, fill it in as in [Setup](#setup-run-locally), then `chmod 600 .env`. Leave out `OLLAMA_URL` and `DB_PATH`: `docker-compose.yml` sets both.
4. **Stop the laptop bot**: `launchctl unload ~/Library/LaunchAgents/com.stockbot.plist && rm ~/Library/LaunchAgents/com.stockbot.plist`. Also stop any copy started by hand (`./run.sh` or `python -m stockbot` in a terminal): `pgrep -fi 'python.* -m stockbot'` must print nothing. Use `-i`: macOS names the process `Python`, so a plain `pgrep -f "python -m stockbot"` misses it, and the two bots then fight over the Telegram token (409 Conflict).
5. **Move the database**, so your locked blueprint keeps its version and old alerts aren't sent again: (Take the backup only after the laptop bot has stopped, or it is out of date at once.)
   ```sh
   # laptop
   sqlite3 data/stockbot.db ".backup stockbot-migrate.db"
   scp stockbot-migrate.db <server>:~/
   # server
   docker volume create stock-bot_stockbot_data
   docker run --rm -v stock-bot_stockbot_data:/data -v ~/stockbot-migrate.db:/src.db:ro alpine \
     sh -c 'cp /src.db /data/stockbot.db && chown -R 1000:1000 /data'
   ```
   `chown -R` matters: the volume's own directory must belong to uid 1000 too, or SQLite can't create its journal file and the log says "attempt to write a readonly database".
6. **Start**: `docker compose up -d --build`, then `docker compose logs -f stock-bot`. Look for "stock bot running" and the Notion sync count, and make sure there's no "Ollama is not reachable" and no 409 Conflict. Send `/blueprint` to check the version.

### Deploys

[.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml) runs the tests on every push. On a push to `main` it also connects to the server and runs `git pull`, `docker compose build stock-bot` and `docker compose up -d stock-bot`, then prunes old images and build cache. The expense-tracker containers aren't touched. In **Settings → Secrets and variables → Actions** of this repository, add the same values expense-tracker uses (secrets belong to each repository):

- `ORACLE_HOST`: the VM's public IP
- `ORACLE_USER`: `ubuntu`
- `ORACLE_SSH_KEY`: the private SSH key
- `ORACLE_SSH_PORT`: only if it isn't 22

Without `ORACLE_HOST` the tests still run and the deploy is skipped.

### Changing settings

The bot reads `~/stock-bot/.env` on the server. To change a value without SSH, add or update a GitHub secret with the same name as the `.env` key, then run **Actions → Apply settings → Run workflow**. It writes those keys into the server `.env`, keeps the previous file as `.env.bak`, and recreates the bot. Keys without a secret are left alone. `OLLAMA_URL`, `DB_PATH` and `TZ` come from `docker-compose.yml`.

A restart alone, including Dozzle's **Restart** button, keeps the old values: Docker reads `.env` only when it creates the container. After editing `.env` by hand, run `docker compose up -d --force-recreate stock-bot`.

### Logs and alerts if the bot stops

- **Logs**: in the expense-tracker Dozzle (`ssh -L 8888:localhost:8888 <server>`, then open http://localhost:8888), or `docker compose logs -f stock-bot`.
- **Heartbeat**: Docker restarts the bot if it crashes, but not if it hangs, and nothing restarts a stopped VM. So the bot pings `HEARTBEAT_URL` after every monitor pass (or `<url>/fail` when the pass fails). Create a free check at [healthchecks.io](https://healthchecks.io) with period 15 minutes and grace 30 minutes, add email or Telegram as the notification, and set its ping URL as `HEARTBEAT_URL`. When the pings stop, healthchecks.io tells you.

### Backups

The server keeps no backup. [scripts/backup.sh](scripts/backup.sh) runs on your **laptop**: it connects over ssh, takes a consistent SQLite online backup inside the running container (no stop), streams it down gzipped, checks that the downloaded copy opens, and keeps 30 days in `~/backups/stock-bot/stockbot-YYYY-MM-DD.db.gz`. Nothing is left on the VM.

Schedule it daily with the launchd agent (10:00; if the laptop is asleep it runs on wake):
```sh
cp deploy/com.stockbot.backup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.stockbot.backup.plist
launchctl start com.stockbot.backup   # run once now; log: ~/backups/stock-bot/backup.log
```
Each snapshot is about 0.4 MB. If the laptop is off for days there is no backup for those days, and a lost VM then loses the changes since the last one. That is the accepted trade-off.

**Restore**: `scp` a snapshot from `~/backups/stock-bot/` on the laptop into `~/stock-bot` on the VM, then:
```sh
cd ~/stock-bot && docker compose stop stock-bot
gunzip -k stockbot-YYYY-MM-DD.db.gz
docker run --rm -v stock-bot_stockbot_data:/data -v "$PWD/stockbot-YYYY-MM-DD.db":/src.db:ro alpine \
  sh -c 'cp /src.db /data/stockbot.db && chown -R 1000:1000 /data'
docker compose start stock-bot
```

### Oracle costs

The tenancy is Pay-As-You-Go. In June 2026 Oracle cut the Always Free Ampere A1 allowance from 4 OCPU / 24 GB to 2 OCPU / 12 GB, and it hasn't clearly said whether existing PAYG VMs above that stay free. Keep a small budget alert (**Billing & Cost Management → Budgets**), and check **Cost Analysis** grouped by SKU: the Ampere A1 lines should cost $0.00. Budgets only alert; they never stop spending.

If charges appear, resize the VM rather than deleting it (**Compute → Instances → Edit → Edit shape**, 2 OCPU / 12 GB). A new VM may not be allowed above the free size. Both bots still fit, because they share one model, but replies take about twice as long, so raise `OLLAMA_TIMEOUT` (to about 180).

### Going back to the laptop

1. On the server, stop the bot and copy its database out of the volume (with the bot stopped, the file is consistent):
   ```sh
   cd ~/stock-bot && docker compose stop stock-bot
   docker run --rm -v stock-bot_stockbot_data:/data:ro -v ~/:/out alpine cp /data/stockbot.db /out/stockbot-final.db
   ```
2. On the laptop: `scp <server>:~/stockbot-final.db data/stockbot.db`, then `./run.sh` or load the launchd agent.

The server bot must stay stopped while the laptop runs (409 Conflict).

## Conviction blueprint

Your blueprint is your own fixed set of investing rules. Every stock is judged by the same rules, so you can always say why you hold a stock and when you would sell it.

### Set it up once

1. In Notion, import [templates/blueprint.csv](templates/blueprint.csv) as a new database (**Import → CSV**). It has 15 starter rules; edit them to match how you invest. Each row has these columns:
   - **Rule**: the rule in your words.
   - **Pillar**: Business, Quality, Growth, Safety or Valuation (any other value is reported and the row skipped).
   - **Metric**: one of ROE, Net margin, Operating margin, Debt/Equity, Current ratio, P/E, Forward P/E, P/B, Price/Graham, Price/Graham IV, PEG, P/E vs 5Y median, P/E contraction, Revenue growth YoY, Profit growth YoY, Revenue CAGR 3Y, Profit CAGR 3Y, Cap-adjusted profit CAGR, EPS beats peers, FCF positive years, Cash conversion, Consecutive loss years, Promoter holding (India only), Dividend yield or From 52w high. *P/E contraction* and *EPS beats peers* are yes/no: use Condition ≥ and Threshold 1. Leave it empty (or write *Judgment*) for a rule you answer yourself, such as *"I understand how the company makes money"*.
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
