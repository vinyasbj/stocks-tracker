# Design

## Context

See proposal.md for the motivation. Today the bot runs from `venv/` through `run.sh` and a launchd agent with hard-coded laptop paths. `config.py` resolves `ROOT` as two levels above `src/stockbot/`, reads `.env` from there if present (environment variables still win), and resolves `DB_PATH` against `ROOT`. Logs go to stdout. The database uses SQLite's default rollback journal, not WAL, so a closed database is a single file. All 165 tests run offline in about 4 s.

The target VM already runs expense-tracker from `~/expense-tracker` as Compose project `expense-tracker`. Its containers are `expense-ollama` (0.40.15, `OLLAMA_KEEP_ALIVE=24h`, port 11434 bound to the host's 127.0.0.1 only), `expense-ollama-init` (pulls `qwen2.5:7b` among others), `expense-bot` and `expense-logs` (Dozzle, reading the Docker socket), all on the network `expense-tracker_expense_net`. Its CI tests on GitHub and then deploys over SSH with `git pull`, `docker compose build` and `up -d`. The image is built on the VM itself, so it is native ARM. From the VM, `curl` returns 200 for SEBI RSS, the NSE equity list and the NSE announcements API.

## Goals / Non-Goals

**Goals:**
- Follow expense-tracker's operational pattern closely, so one mental model covers both bots.
- Make deploying the bot leave expense-tracker's containers untouched.
- Make the first server start continue from the laptop's state.

**Non-Goals:**
- Handling Telegram updates concurrently (PTB `concurrent_updates`). Updates stay sequential, as on the laptop; only a timeout is added.
- Building images in CI or pushing them to a registry.
- A linter gate in CI.
- Any HTTP endpoint or inbound port.

## Decisions

### D1. Image: `python:3.12-slim-bookworm`, source in `/app/src`, run as uid 1000
The laptop runs Python 3.12 and the tests pass there, so the image uses 3.12, not expense-tracker's 3.11. Layout: `WORKDIR /app`, copy the locked `requirements.txt` and install it first (layer cache), then copy `src/` only. `templates/` and `deploy/` aren't read at runtime. `ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1`; `CMD ["python", "-m", "stockbot"]`. `config.ROOT` then resolves to `/app`, so the default `DB_PATH` lands in `/app/data`, the volume mount. apt installs `tzdata` (log time zone; `zoneinfo` has the system data) and `procps` (for `pgrep` in the health check), as expense-tracker does. `useradd -u 1000 appuser` and `chown /app` come before `USER appuser`.

Health check: `pgrep -f "python -m stockbot"`, the same liveness-only check as expense-tracker. *Alternative*: a heartbeat file written by the job queue. More accurate, but it needs app code and a second convention, so it is deferred.

### D2. Share expense-tracker's Ollama over its Docker network
`docker-compose.yml` pins `name: stock-bot` so volume and network names don't depend on the checkout directory. It declares an external network:

```yaml
networks:
  expense_net:
    external: true
    name: expense-tracker_expense_net
```

`OLLAMA_URL=http://expense-ollama:11434` is set in `environment:`, which takes precedence over `env_file`. Both bots send `qwen2.5:7b` with `num_ctx=8192`, which Ollama treats as the same model configuration, so a single copy stays loaded (keep-alive 24h).

*Alternatives*: (a) The host port through `host.docker.internal:host-gateway`. This doesn't work: Ollama's port is bound to the host's `127.0.0.1`, which a container can't reach through the bridge gateway, and rebinding it to `0.0.0.0` would expose it beyond localhost. (b) A separate Ollama. Rejected: about 5 GB more RAM and the same four cores in contention. (c) A smaller second model for this bot (`qwen2.5:3b`). Measured on the VM: it halves `/why` (66–71 s against 132–139 s on the same prompt), but its text ignores the format (5–7 sentences, restates the verdict, lists passing rules), it is likely weaker on the structured calls (intent, news impact, regulator relevance; not tested), and `expense-ollama` runs with `OLLAMA_MAX_LOADED_MODELS=3` while expense-tracker already uses three models (`qwen2.5:1.5b`, `moondream`, `qwen2.5:7b`). A fourth model would make Ollama evict one of them, and a reload of `qwen2.5:7b` (about 8 s) risks expense-bot's 8–30 s timeouts. Rejected; shortening the prompt (D10) gives a larger gain on the model that is already loaded.

*Consequence*: Compose `depends_on` can't cross projects, so the bot may start before Ollama is ready. That is already handled: startup only warns, and each call fails individually (D3).

### D3. Bound each model call with `OLLAMA_TIMEOUT` (code default 180 s, 90 s on the server)
`LLM(...)` gains a `timeout` that is passed to `ollama.AsyncClient(host=..., timeout=...)`, which forwards it to httpx. `Settings` gains `ollama_timeout: float`, read from `OLLAMA_TIMEOUT`. A timeout raises inside the existing `except Exception` paths in handlers and engine, so the user gets the existing error reply instead of a bot that stops responding. The default of 180 s is a placeholder. No published benchmark covers 7B on this VM shape: the closest found was `qwen2.5:3b` at about 8–12 tokens/s on a 4-core A1 under Ollama, which suggests roughly 4–6 tokens/s for 7B. **Measured on the VM** (4-core Ampere A1, CPU only, `qwen2.5:7b` Q4, `num_ctx` 8192): prompt evaluation 19.5–21 tokens/s and generation 5.2–6.6 tokens/s, so reading the prompt dominates. The real `/why` prompt was about 2,000 tokens, not 6–8k, and took 132–139 s end to end. After the D10 trim (800–1,100 tokens) the five portfolio stocks tried took 41–53 s cold, and 28 s when Ollama still held part of the prompt. The timeout is the slowest cold run plus about 50%, rounded up: **90 s**. The boot volume has 39 GB free. Before go-live the timeout is set from a measurement on the VM. `ollama run qwen2.5:7b --verbose` gives the prompt-eval and eval rates. The timeout is then the time for the longest real prompt (a `/why` explanation with an 8k context, measured end to end) with about 50% headroom. It can be retuned through Apply settings without a deploy.

### D4. Locked requirements generated in the server's platform
`requirements.in` holds today's direct pins without `pytest`. `scripts/lock_requirements.sh` runs `pip-compile` inside `python:3.12-slim-bookworm`; Docker on the Apple-silicon laptop is arm64 Linux, like the VM. It emits versions without hashes, so CI's x86-64 runner installs the same versions. `requirements-dev.txt` is `-r requirements.txt` plus `pytest==9.2.0`. The local `venv` and README setup switch to `requirements-dev.txt`. `truststore` stays: on Linux it uses the system CA store, the same one `curl` used on the VM.

### D5. CI/CD mirrors expense-tracker, scoped to the bot
`.github/workflows/ci-cd.yml`:
- **test** (push and pull_request on `main`): Python 3.12, `pip install -r requirements-dev.txt`, `python -m compileall -q src`, `pytest -q`.
- **deploy** (push to `main` only, `needs: test`, `concurrency: oracle-deploy`, skipped with a message when `ORACLE_HOST` is empty): `appleboy/ssh-action@v1.0.3` with the same secret names and defaults as expense-tracker, running:
  ```sh
  set -e; cd ~/stock-bot
  git pull --ff-only origin main
  docker compose build stock-bot
  docker compose up -d stock-bot
  docker image prune -f
  docker builder prune -f --filter until=168h
  ```
  `--ff-only` makes the deploy fail loudly if someone edited the server checkout. `docker image prune` leaves BuildKit's cache in place, and on a 50 GB boot volume that already holds about 8 GB of Ollama models plus expense-tracker's image, the cache from repeated builds is what grows. Pruning cache older than a week keeps recent layers, so routine deploys stay fast. Naming the service keeps the step from touching other projects. Concurrency groups are per repo, so the two repos' deploys can overlap. That is harmless because they share no containers or volumes.

`.github/workflows/apply-settings.yml` is a copy of expense-tracker's with this bot's key list (every key in `.env.example` except `OLLAMA_URL` and `DB_PATH`, which Compose overrides, plus `OLLAMA_TIMEOUT`). It works in `~/stock-bot` and finishes with `docker compose up -d --force-recreate --no-deps stock-bot`.

### D6. Backups use SQLite's online backup, not a tar of the volume
`scripts/backup.sh` runs on the investor's laptop (decided 2026-11-11, replacing a VM cron job plus an Object Storage upload). Over ssh it runs `docker exec stock-bot python` with `sqlite3`'s `Connection.backup` into a temp file inside the container, streams the file down gzipped, runs `integrity_check` on the downloaded copy, removes the temp file, and deletes snapshots older than `KEEP_DAYS` (30). *Why not a tar of the volume*: a tar of a live SQLite file can capture a half-written page, while the backup API gives a consistent copy without stopping the bot. A launchd agent (`deploy/com.stockbot.backup.plist`) runs it daily at 10:00.

**No VM-side copy.** The investor chose laptop-only backups for now: no cron job on the VM, no Object Storage bucket, no pre-authenticated request to expire. *Trade-off*: the laptop is not always on, so days can be missed, and the VM and laptop failing together loses the changes since the last copy. Revisit with an Object Storage upload if that proves too thin.



Size: each snapshot is about 0.4 MB gzipped, so 30 days is about 12 MB.

Restore (README): `docker compose stop stock-bot`, then `scp` a snapshot from the laptop's `~/backups/stock-bot/`, unzip it, copy it into the volume with a throwaway `alpine` container and `chown -R 1000:1000 /data`, then `docker compose start stock-bot`.

### D9. Heartbeat from the monitor job (dead-man's switch)
`Settings.heartbeat_url: str = ""`, read from `HEARTBEAT_URL`. In `handlers.monitor_job`, after a successful `engine.check(...)` the bot sends `GET heartbeat_url`. In the `except` branch it sends `GET heartbeat_url + "/fail"`. Each ping is an `httpx.AsyncClient` call with a 10 s timeout, wrapped in its own `try/except` that only logs, so the heartbeat can't break a pass. The healthchecks.io check is set to period 15 min (`POLL_MINUTES`) and grace 30 min, with notifications to email or Telegram, configured in healthchecks.io.

*Why the monitor job*: it's the bot's own 15-minute loop through the job queue. A ping from it proves that the process is up, polling is alive, Notion and Yahoo work, and the VM is running and online. A ping from cron or Docker would prove less. *Why not fix Docker's unhealthy-no-restart instead*: an autoheal sidecar would restart a hung bot but still wouldn't report a stopped VM. The heartbeat covers both, and no extra container is needed.

### D10. A short `/why` prompt, a capped reply and a cache
Measured on the VM, 75–80% of a `/why` was the model reading its prompt, and the prompt was mostly content it was told not to use (ten passing or unassessed rule lines, five quarters and four years of tables, twelve headlines). The handler already sends the verdict and rules first and edits the explanation in afterwards, so the wait is only for the prose.

- **Prompt** (`conviction.explain_data`, replacing `data_block` + `summary_data` for this call only; `data_block` still feeds the other commands, unchanged): stock and sector, the latest quarter and its changes (with the out-of-date warning), at most 5 headlines with filing text cut to 120 characters, the rules that fail or could not be checked (judgment rules not yet confirmed are one grouped line), only a count of the passing rules, the verdict, whether the stock is held, and the thesis (3 lines per section). No price, market cap, older quarters or annual table.
- **Reply** (`LLM.chat(max_tokens=...)`, `WHY_MAX_TOKENS = 150`): passed to Ollama as `num_predict`. The system prompt also asks for at most 70 words. When Ollama reports that the reply stopped at the cap (`done_reason == "length"`), the unfinished last sentence is dropped. The reply was already cut to three sentences.
- **Cache** (`WHY_TTL` 7 days, in the existing SQLite `cache` table): the key is the hash of the model name, system prompt and data, so it follows exactly what the model reads. Price is not in the data, so a price move alone does not miss. A failed or empty reply is not stored.

*Trade-off*: five headlines instead of twelve means the explanation can miss news further down the list (in one NVDA run the full prompt mentioned a legal story the short one could not see). `EXPLAIN_NEWS` is one constant; each headline costs about 30 tokens, about 1.5 s. *Alternatives*: pre-generating explanations overnight for every holding (the same CPU work at a quiet hour, so most `/why` calls would be instant), and having code write the failing-rule sentence so the model covers only news and what to watch. Neither is built.

### D7. One-time data migration before the first start
The bot creates an empty database at first start, and its first monitor pass would then record a new baseline. So the data must be in the volume before `up`. Laptop: `launchctl unload` the agent, then `sqlite3 data/stockbot.db ".backup stockbot-migrate.db"`, then `scp` the file to the VM. VM: `docker volume create stock-bot_stockbot_data`, then use `alpine` to copy it in as `/data/stockbot.db` and `chown -R 1000:1000 /data`, then `docker compose up -d`. The `-R` and the directory matter: a new volume's root is owned by root, and without write access to the directory SQLite can't create its journal file ("attempt to write a readonly database"), even when the database file itself belongs to uid 1000. The laptop bot must be stopped before the backup is taken, and checked with a case-insensitive `pgrep -fi`: macOS names the process `Python`, so a case-sensitive match misses it, and a bot left running would both keep changing the data and fight the server over the Telegram token.

### D8. Release: commit conviction-blueprint, then add deployment, then merge
1. Commit the current working tree on `conviction-blueprint` as it is: the finished feature code, its artifacts, and `conviction-upkeep`'s planning documents, which are only text.
2. Add this change's files on the same branch, run the tests, and push.
3. Bootstrap the server by hand (D7, `.env`, the first `up`), then add the GitHub secrets.
4. Fast-forward `main` to the branch and push. `main` (`e1fcf51`) is an ancestor, so this is a clean fast-forward. The push runs CI and the first automatic deploy, which should be a no-op rebuild.

`conviction-blueprint`'s remaining manual task (8.9, the Telegram retest with a single instance polling) is done against the server bot after step 4.

## Risks / Trade-offs

- [Oracle halved the Always Free Ampere A1 allowance to 2 OCPU / 12 GB in June 2026. Whether existing Pay-As-You-Go 4/24 VMs stay free is disputed: reported extra cost is about $25–28 a month] → The tenancy is PAYG with a $0.10 budget alert, which catches the first charge. Budgets only notify; they don't cap spending, and cost data lags by hours. If charges appear, resize the VM to 2 OCPU / 12 GB (Edit shape keeps the boot volume). Both bots still fit, because they share one model, but model calls slow down about 2x, so retune `OLLAMA_TIMEOUT`. Don't terminate the VM: a new one may not be allowed above 2/12. The laptop backups (D6) make losing the VM survivable.
- [A Docker container marked unhealthy is never restarted by `restart: unless-stopped`] → Accepted. The heartbeat (D9) reports a hang or a stopped VM from outside, and the investor restarts the bot from Dozzle or over SSH.
- [A long stock-bot model call delays expense-bot, whose Ollama timeouts are 8-30 s] → Both bots use one loaded model, so there is no load cost, only queueing. A `/why` is now about 30–55 s, not about 135 s (D10), so the queue is shorter. Watch Dozzle for expense-bot timeouts after go-live. If they appear, set `OLLAMA_NUM_PARALLEL=2` on `expense-ollama` in expense-tracker; that change belongs to that repo, not this one.
- [Expense-tracker's network or container is renamed, which breaks the bot's Ollama access] → `docker compose up` fails fast when an external network is missing. The compose file comments name the dependency, and so does expense-tracker's model list.
- [Expense-tracker stops pulling `qwen2.5:7b`] → The startup warning "Ollama has no model" appears in the log. The README notes the dependency.
- [NSE starts blocking the VM's IP; it answered 200 when tested] → Filings fetches already fail per item and are logged. Watch the first few monitor passes.
- [The laptop agent is accidentally reloaded] → Telegram 409 Conflict appears in both logs. The plist header and README say it is laptop-only and must stay unloaded while the server runs.
- [The health check proves only that the process exists, not that polling works] → Kept, to match expense-tracker and for Dozzle's status display. The real signal is the heartbeat (D9).
- [Running `docker compose down` in expense-tracker tries to delete the shared network while stock-bot is attached] → Compose refuses and warns, and expense-tracker's CI uses only `up -d`. The README for this repo says to run `docker compose up -d` here after any expense-tracker `down`/`up`, which reattaches the network.
- [`git pull` on the server needs read access to `vinyasbj/stocks-tracker`] → The bootstrap clones it the way `~/expense-tracker` was cloned, over HTTPS for a public repo or with a deploy key for a private one.

## Migration Plan

1. Implement the files (tasks §1–§4) and get green tests locally.
2. Server bootstrap (tasks §5): clone to `~/stock-bot`, confirm the network name, write `.env`, migrate the database (D7), `docker compose up -d --build`, and check the log and the commands in Telegram.
3. GitHub: add the secrets, then fast-forward `main` (D8).
4. Load the laptop's backup agent and take one snapshot by hand (verified to open locally).
5. Create the healthchecks.io check and set `HEARTBEAT_URL` through Apply settings.

**Rollback**: `docker compose down` in `~/stock-bot` (the volume stays). To go back to the laptop, copy the server database back with the backup script's snapshot if it has newer state, then `launchctl load` the agent. Expense-tracker isn't touched by any rollback step.
