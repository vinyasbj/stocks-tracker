# Proposal

## Why

The bot runs only on the investor's laptop, so alerts, the 08:30 IST digest and every command stop whenever the laptop sleeps. The investor already runs the expense-tracker bot on an Oracle Cloud Always Free VM (4 ARM vCPU, 24 GB) with Docker Compose, a shared Ollama, and GitHub Actions deploys. The conviction-blueprint feature is finished, so this is the point to move the bot to that server the same way, before `conviction-upkeep` begins.

## What Changes

- **Container image**: a `Dockerfile` (Python 3.12 slim Bookworm, non-root user, `tzdata`, a process health check) and a `.dockerignore` that keeps `.env`, `data/`, `venv/`, tests and `openspec/` out of the image.
- **Compose stack**: a `docker-compose.yml` with one service, `stock-bot`, and a named volume for `data/` (SQLite). The bot joins expense-tracker's existing Docker network and uses its Ollama container (`expense-ollama`). Both bots use `qwen2.5:7b` with `num_ctx=8192`, so one loaded copy of the model serves both. The change adds no second Ollama and no second Dozzle: the existing Dozzle already shows every container on the host.
- **CI/CD**: a GitHub Actions workflow runs the test suite on each push and pull request. On a push to `main` it connects to the VM over SSH, pulls, rebuilds and restarts `stock-bot`, using the same `ORACLE_*` secrets as expense-tracker.
- **Settings without SSH**: an "Apply settings" workflow copies the bot's `.env` keys from GitHub Secrets into the server's `.env` and recreates the container, as in expense-tracker.
- **Pinned dependencies**: `requirements.in` lists the direct dependencies, and `requirements.txt` becomes a full lock generated in the same Linux image. `pytest` moves to `requirements-dev.txt` so it stays out of the runtime image.
- **Backups**: a script that takes a consistent SQLite snapshot of the bot's volume each night from cron and keeps 30 days. Each snapshot is also uploaded off the VM to an OCI Object Storage bucket through a write-only pre-authenticated URL. A copy kept only on the VM would be lost with the VM.
- **Dead-man's switch**: after each monitor pass the bot pings an optional `HEARTBEAT_URL` (for example a free healthchecks.io check), or its `/fail` URL when the pass fails. When pings stop, the service notifies the investor. Docker restarts only a process that exits, never one that hangs, and the container health check only proves the process exists.
- **Measured model timeout**: before go-live, measure `qwen2.5:7b` speed on the VM and set `OLLAMA_TIMEOUT` from the measurement.
- **Disk hygiene**: each deploy also removes old Docker build cache, which `docker image prune` leaves behind, so repeated builds don't fill the 50 GB boot volume.
- **One-time migration**: the laptop's `data/stockbot.db` (locked blueprint v1, 1,591 seen alert items, cache) is copied into the server volume, so verdicts keep naming v1 and the first monitor pass does not re-alert.
- **Single instance**: the laptop launchd agent is retired. Two processes polling the same bot token make Telegram return 409 Conflict.
- **Release scope**: the `conviction-blueprint` branch, which holds uncommitted work, is committed and merged into `main`, and that is what gets deployed. `conviction-upkeep` stays unimplemented for later.
- **Docs**: the README describes the server deployment, secrets, settings changes, logs, backups and restore. Laptop use becomes the "run locally" option.

Out of scope: running Ollama for this bot alone; serving anything over HTTP (the bot only makes outbound connections); a lint gate (the repo has no linter configuration yet); and `conviction-upkeep`.

## Capabilities

### New Capabilities

- `server-deployment`: how the bot is built, configured, run, updated, backed up and kept to a single instance on the shared server.

### Modified Capabilities

(none: the bot's commands and verdicts behave the same on the server as on the laptop)

## Impact

- **New files**: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `requirements.in`, `requirements-dev.txt`, `scripts/lock_requirements.sh`, `scripts/backup.sh`, `.github/workflows/ci-cd.yml`, `.github/workflows/apply-settings.yml`.
- **Changed files**: `requirements.txt` (full lock, without pytest); `README.md`; `.env.example` (notes on which keys the server overrides); `deploy/com.stockbot.plist` (marked laptop-only, with instructions to unload it); `run.sh` unchanged.
- **Application code**: two small additions. (1) A new `OLLAMA_TIMEOUT` setting (default 180 s, tuned after measurement) bounds each model call, because the bot handles updates one at a time and CPU-only inference on the VM is slower than on the laptop. Without it, a stuck call would block every command. (2) An optional `HEARTBEAT_URL` is pinged by the monitor job. Nothing else changes: settings already come from environment variables, and Compose sets `DB_PATH` and `OLLAMA_URL`. Touches `config.py`, `llm/client.py`, `engine.py` (where the client is built), `bot/handlers.py` (monitor job) and `.env.example`.
- **Oracle account**: the tenancy is Pay-As-You-Go with a $0.10 budget alert. Oracle cut the Always Free Ampere A1 allowance to 2 OCPU / 12 GB in June 2026, and whether existing PAYG 4/24 VMs stay free is unconfirmed. The budget alert is the tripwire. It only notifies and does not cap spending. The new Object Storage use (a few MB) stays far inside the free 20 GB.
- **Server**: a new checkout at `~/stock-bot` beside `~/expense-tracker`, a new `.env`, one cron entry, and a dependency on the expense-tracker stack being up (its Ollama and its network). That stack's `ollama-init` already pulls `qwen2.5:7b`.
- **External services**: an OCI Object Storage bucket with a write-only pre-authenticated request, and a free healthchecks.io check (or any URL that accepts a GET ping).
- **GitHub (`vinyasbj/stocks-tracker`)**: the `ORACLE_HOST`, `ORACLE_USER`, `ORACLE_SSH_KEY` and optional `ORACLE_SSH_PORT` secrets, plus the bot's settings secrets. Secrets belong to each repo, so expense-tracker's are not inherited.
- **Shared resources**: the two bots queue on one Ollama instance and four CPU cores, so a long `/report` can delay an expense-bot reply, and the other way round.
