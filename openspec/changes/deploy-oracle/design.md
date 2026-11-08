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

*Alternatives*: (a) The host port through `host.docker.internal:host-gateway`. This doesn't work: Ollama's port is bound to the host's `127.0.0.1`, which a container can't reach through the bridge gateway, and rebinding it to `0.0.0.0` would expose it beyond localhost. (b) A separate Ollama. Rejected: about 5 GB more RAM and the same four cores in contention.

*Consequence*: Compose `depends_on` can't cross projects, so the bot may start before Ollama is ready. That is already handled: startup only warns, and each call fails individually (D3).

### D3. Bound each model call with `OLLAMA_TIMEOUT` (default 180 s)
`LLM(...)` gains a `timeout` that is passed to `ollama.AsyncClient(host=..., timeout=...)`, which forwards it to httpx. `Settings` gains `ollama_timeout: float`, read from `OLLAMA_TIMEOUT`. A timeout raises inside the existing `except Exception` paths in handlers and engine, so the user gets the existing error reply instead of a bot that stops responding. The default of 180 s is a placeholder. No published benchmark covers 7B on this VM shape: the closest found was `qwen2.5:3b` at about 8–12 tokens/s on a 4-core A1 under Ollama, which suggests roughly 4–6 tokens/s for 7B. Before go-live the timeout is set from a measurement on the VM. `ollama run qwen2.5:7b --verbose` gives the prompt-eval and eval rates. The timeout is then the time for the longest real prompt (a `/why` explanation with an 8k context, measured end to end) with about 50% headroom. It can be retuned through Apply settings without a deploy.

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
`scripts/backup.sh` runs `docker exec stock-bot python -c` with `sqlite3`'s `Connection.backup` to write `/tmp/stockbot-snap.db` inside the container, `docker cp`s it to `~/backups/stock-bot/stockbot-YYYY-MM-DD.db`, gzips it, and deletes snapshots older than `KEEP_DAYS` (30). *Why not expense-tracker's tar*: a tar of a live SQLite file can capture a half-written page, while the backup API gives a consistent copy without stopping the bot. Cron runs at 02:45 server time, after expense-tracker's backup. A separate directory keeps the two retention globs apart.

**Off-VM copy.** After writing the local `.db.gz`, the script uploads it with `curl -fsS -T <file> "$BACKUP_PAR_URL<name>"`. `BACKUP_PAR_URL` is a pre-authenticated request (PAR) on a private OCI Object Storage bucket (Standard tier, home region). It is scoped to object writes only, with a long expiry (for example 1 year), and its expiry date is noted in the README. The bucket gets a lifecycle rule that deletes objects after 90 days. The URL lives in a root-readable file on the VM (`~/stock-bot/.backup.env`, mode 600, git-ignored) that the script sources. It is not in the bot's `.env`, so the container never sees it. If the upload fails, the script keeps the local file and exits non-zero, so cron's log shows the failure.

*Why a PAR and not the OCI CLI*: a PAR needs only `curl`, adds no credentials that can read or delete, and can't touch other resources. A compromised VM can at worst add objects to that bucket. *Alternatives*: (a) The laptop pulls with `scp`. Free, but it depends on the laptop being awake, which is the problem this change exists to fix. (b) OCI boot-volume backups (5 free). These cover the whole disk but are coarse and slow to restore, and they still live in the same tenancy and region. Object Storage is in the same tenancy too, but it outlives the VM, and the investor can download from it.

Size: each snapshot is about 0.5 MB gzipped, so 90 days is under 50 MB, far inside the free 20 GB and the free monthly request quota.

Restore (README): `docker compose stop stock-bot`, then get the snapshot (from `~/backups/stock-bot/`, or from the bucket in the OCI console or with `curl` on a read PAR made at restore time), unzip it, copy it into the volume with a throwaway `alpine` container and `chown 1000:1000`, then `docker compose start stock-bot`.

### D9. Heartbeat from the monitor job (dead-man's switch)
`Settings.heartbeat_url: str = ""`, read from `HEARTBEAT_URL`. In `handlers.monitor_job`, after a successful `engine.check(...)` the bot sends `GET heartbeat_url`. In the `except` branch it sends `GET heartbeat_url + "/fail"`. Each ping is an `httpx.AsyncClient` call with a 10 s timeout, wrapped in its own `try/except` that only logs, so the heartbeat can't break a pass. The healthchecks.io check is set to period 15 min (`POLL_MINUTES`) and grace 30 min, with notifications to email or Telegram, configured in healthchecks.io.

*Why the monitor job*: it's the bot's own 15-minute loop through the job queue. A ping from it proves that the process is up, polling is alive, Notion and Yahoo work, and the VM is running and online. A ping from cron or Docker would prove less. *Why not fix Docker's unhealthy-no-restart instead*: an autoheal sidecar would restart a hung bot but still wouldn't report a stopped VM. The heartbeat covers both, and no extra container is needed.

### D7. One-time data migration before the first start
The bot creates an empty database at first start, and its first monitor pass would then record a new baseline. So the data must be in the volume before `up`. Laptop: `launchctl unload` the agent, then `sqlite3 data/stockbot.db ".backup stockbot-migrate.db"`, then `scp` the file to the VM. VM: `docker volume create stock-bot_stockbot_data`, then use `alpine` to copy it in as `/data/stockbot.db` and `chown 1000:1000`, then `docker compose up -d`.

### D8. Release: commit conviction-blueprint, then add deployment, then merge
1. Commit the current working tree on `conviction-blueprint` as it is: the finished feature code, its artifacts, and `conviction-upkeep`'s planning documents, which are only text.
2. Add this change's files on the same branch, run the tests, and push.
3. Bootstrap the server by hand (D7, `.env`, the first `up`), then add the GitHub secrets.
4. Fast-forward `main` to the branch and push. `main` (`e1fcf51`) is an ancestor, so this is a clean fast-forward. The push runs CI and the first automatic deploy, which should be a no-op rebuild.

`conviction-blueprint`'s remaining manual task (8.9, the Telegram retest with a single instance polling) is done against the server bot after step 4.

## Risks / Trade-offs

- [Oracle halved the Always Free Ampere A1 allowance to 2 OCPU / 12 GB in June 2026. Whether existing Pay-As-You-Go 4/24 VMs stay free is disputed: reported extra cost is about $25–28 a month] → The tenancy is PAYG with a $0.10 budget alert, which catches the first charge. Budgets only notify; they don't cap spending, and cost data lags by hours. If charges appear, resize the VM to 2 OCPU / 12 GB (Edit shape keeps the boot volume). Both bots still fit, because they share one model, but model calls slow down about 2x, so retune `OLLAMA_TIMEOUT`. Don't terminate the VM: a new one may not be allowed above 2/12. The off-VM backups (D6) make losing the VM survivable.
- [A Docker container marked unhealthy is never restarted by `restart: unless-stopped`] → Accepted. The heartbeat (D9) reports a hang or a stopped VM from outside, and the investor restarts the bot from Dozzle or over SSH.
- [A long stock-bot model call delays expense-bot, whose Ollama timeouts are 8-30 s] → Both bots use one loaded model, so there is no load cost, only queueing. Watch Dozzle for expense-bot timeouts after go-live. If they appear, set `OLLAMA_NUM_PARALLEL=2` on `expense-ollama` in expense-tracker; that change belongs to that repo, not this one.
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
4. Create the Object Storage bucket and PAR, install the backup cron job, and take one snapshot by hand (local and uploaded).
5. Create the healthchecks.io check and set `HEARTBEAT_URL` through Apply settings.

**Rollback**: `docker compose down` in `~/stock-bot` (the volume stays). To go back to the laptop, copy the server database back with the backup script's snapshot if it has newer state, then `launchctl load` the agent. Expense-tracker isn't touched by any rollback step.
