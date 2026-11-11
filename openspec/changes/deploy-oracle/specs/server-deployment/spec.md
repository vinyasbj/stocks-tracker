# Spec Delta

## Purpose

Running the bot unattended on the investor's shared Oracle Cloud server: how it is started and kept running, where its settings and data live, how a new version reaches the server, how its data is backed up, and how it shares the local model with the expense-tracker bot.

## ADDED Requirements

### Requirement: The bot runs as a long-lived service on the server
The bot SHALL run on the server as a container that starts when the host or Docker starts, restarts after a crash, and runs as a non-root user. Its log SHALL go to the container's standard output and be rotated, so it is visible in the server's existing log viewer.

#### Scenario: Crash
- **WHEN** the bot process exits unexpectedly
- **THEN** the container restarts it without manual action

#### Scenario: Server reboot
- **WHEN** the VM reboots
- **THEN** the bot is running again once Docker has started, without anyone logging in

#### Scenario: Logs
- **WHEN** the investor opens the server's log viewer
- **THEN** the bot's log appears beside the expense-tracker containers, with older log files rotated out

### Requirement: A stalled bot is reported from outside the server
When `HEARTBEAT_URL` is set, the bot SHALL send a GET request to it after each successful monitor pass, and to `HEARTBEAT_URL/fail` after a failed pass. Heartbeat errors SHALL be logged and SHALL NOT affect the monitor pass. Without `HEARTBEAT_URL` the bot SHALL send no heartbeat.

#### Scenario: Healthy passes
- **WHEN** monitor passes succeed every 15 minutes
- **THEN** the heartbeat service receives a ping for each one and sends no notification

#### Scenario: Bot stalls or the VM stops
- **WHEN** no ping arrives within the check's period plus grace time
- **THEN** the heartbeat service notifies the investor, independently of the VM

#### Scenario: Pass fails
- **WHEN** a monitor pass raises an error
- **THEN** the bot pings `HEARTBEAT_URL/fail` and the service reports the failure

#### Scenario: Heartbeat service unreachable
- **WHEN** the ping itself fails
- **THEN** the bot logs a warning and the next monitor pass runs as usual

### Requirement: Scheduled jobs keep India time on the server
The daily digest SHALL arrive at `DIGEST_TIME_IST` in Asia/Kolkata time, whatever the server's own time zone is.

#### Scenario: Server clock in UTC
- **WHEN** the VM's clock is UTC and `DIGEST_TIME_IST` is 08:30
- **THEN** the digest is sent at 08:30 IST (03:00 UTC)

### Requirement: The bot uses the server's shared local model
The bot SHALL send its model requests to the expense-tracker stack's Ollama instance over a private Docker network, using the same model and context size as that bot, so the server keeps a single loaded copy of the model. Ollama SHALL NOT be reachable from the internet.

#### Scenario: Model available
- **WHEN** the expense-tracker stack is running with `qwen2.5:7b` pulled
- **THEN** `/why` explanations and free-text answers are produced, and the server does not load a second copy of the model

#### Scenario: No extra model
- **WHEN** the bot answers any request
- **THEN** it uses only the configured model (`qwen2.5:7b`), so the server's limit of three loaded models, which the expense-tracker stack's own three models already fill, is not exceeded

#### Scenario: Shared Ollama down
- **WHEN** the expense-tracker Ollama is not running
- **THEN** the bot still starts and logs that Ollama is unreachable; data-only replies such as `/report` and `/portfolio` still work, and model-written parts fail with an error message instead of hanging the bot

### Requirement: `/why` answers within a bounded time on the server's CPU
The model's explanation of a verdict SHALL be built from a short prompt and a capped reply, so that it finishes well inside `OLLAMA_TIMEOUT` on the server's CPU-only model, and SHALL be reused while nothing it reads has changed. The verdict, the rule results and every number shown SHALL come from code, not from the model.

#### Scenario: What the prompt holds
- **WHEN** the explanation prompt is built
- **THEN** it contains the rules that fail or could not be checked, the verdict, the investor's thesis, the latest quarter and at most five recent headlines
- **AND** it only counts the rules that pass, and has no older quarters, annual tables or price

#### Scenario: Capped reply
- **WHEN** the model's reply reaches the limit of about 150 tokens
- **THEN** the reply loses its unfinished sentence instead of ending mid-sentence

#### Scenario: Fresh `/why` on the server
- **WHEN** the investor asks `/why` for a stock whose explanation is not cached
- **THEN** the verdict and rules appear at once, and the model's explanation is added within `OLLAMA_TIMEOUT` (measured on the VM: 28–53 s for `qwen2.5:7b`, against 132–139 s with the full data block)

#### Scenario: Asked again
- **WHEN** `/why` is asked again for a stock whose rules, latest quarter, headlines and thesis have not changed
- **THEN** the earlier explanation is shown without calling the model, even if the price moved

#### Scenario: Something the explanation reads changed
- **WHEN** a new headline, a new quarter, a rule result, the thesis, the prompt wording or the model changes
- **THEN** the model is asked again

#### Scenario: Model fails
- **WHEN** the model call fails or returns nothing
- **THEN** the investor sees the existing error text or an empty summary, and nothing is cached, so the next `/why` asks the model again

### Requirement: Settings come from a server-side environment file, never the image
The bot's secrets and settings SHALL be read from a `.env` file on the server that is excluded from git and from the image. The server-specific values (Ollama address, database path) SHALL be set by the deployment and SHALL override `.env`.

#### Scenario: Image contents
- **WHEN** the image is built from a working copy that contains `.env` and `data/`
- **THEN** neither is in the image

#### Scenario: Change a setting from GitHub
- **WHEN** the investor sets a GitHub secret named after a `.env` key (for example `NOTION_BLUEPRINT_DB`) and runs the "Apply settings" workflow
- **THEN** that key is written into the server's `.env`, the previous file is kept as `.env.bak`, other keys are left unchanged, and the bot is recreated so it loads the new value

#### Scenario: Multi-line secret
- **WHEN** a settings secret contains a line break
- **THEN** the workflow fails without changing the server's `.env`

### Requirement: Bot data persists across deploys
The SQLite database (locked blueprint versions, seen alert items, holdings, cache, chat history) SHALL live in a named volume that survives rebuilding, recreating and redeploying the container.

#### Scenario: Redeploy keeps blueprint version
- **WHEN** blueprint v1 is locked and a new version of the bot is deployed
- **THEN** `/blueprint` still reports v1, and the next monitor pass does not re-alert items it had already seen

### Requirement: Laptop data is carried over once
At the first deployment the laptop's existing database SHALL be copied into the server's data volume while no instance of the bot is running, so the locked blueprint keeps its version number and alert history continues.

#### Scenario: First start on the server
- **WHEN** the bot starts on the server for the first time after the copy
- **THEN** `/blueprint` reports the same locked version as the laptop did, and the first monitor pass is not treated as a new baseline

### Requirement: Only one instance polls the bot token
Exactly one bot process SHALL poll the Telegram token. Once the server instance runs, the laptop's auto-start agent SHALL be removed, and the README SHALL say how to run locally without conflicting with the server.

#### Scenario: Laptop agent retired
- **WHEN** the server instance is running
- **THEN** the laptop no longer starts the bot at login, and the server bot's log shows no Telegram 409 Conflict errors

### Requirement: Pushes to main are tested and deployed automatically
Every push and pull request SHALL run the test suite. A push to `main` whose tests pass SHALL update the server's checkout to that commit, rebuild the bot's image and restart only the bot. A failing test SHALL stop the deploy. Deploys to the server SHALL not run concurrently.

#### Scenario: Green push to main
- **WHEN** a commit is pushed to `main` and the tests pass
- **THEN** the server runs that commit within the workflow run, and the expense-tracker containers are not restarted

#### Scenario: Red push
- **WHEN** a test fails
- **THEN** nothing is deployed and the server keeps running the previous version

#### Scenario: Server not configured
- **WHEN** the `ORACLE_HOST` secret is not set
- **THEN** the tests still run and the deploy step is skipped with a message saying why

#### Scenario: Pull request
- **WHEN** a pull request is opened against `main`
- **THEN** the tests run and nothing is deployed

### Requirement: Builds are reproducible
The image and CI SHALL install the same fully pinned dependency versions, generated for the server's Linux platform. Test-only tools SHALL NOT be installed in the runtime image.

#### Scenario: Same versions everywhere
- **WHEN** CI and the server build from the same commit
- **THEN** both install identical package versions, and the runtime image has no pytest

### Requirement: The database is backed up nightly
A nightly job on the server SHALL save a consistent snapshot of the bot's database outside the volume, keep the last 30 days on the VM, and upload each snapshot to storage outside the VM. The upload credential SHALL allow writing only. The README SHALL describe how to restore a snapshot from either place.

#### Scenario: Nightly snapshot
- **WHEN** the nightly job runs while the bot is running
- **THEN** a dated, readable copy of the database is saved on the VM and uploaded off the VM, and local snapshots older than 30 days are deleted

#### Scenario: Off-VM upload fails
- **WHEN** the upload fails, for example because the network is down or the upload URL has expired
- **THEN** the local snapshot is still kept, and the job exits non-zero and logs the failure

#### Scenario: VM lost
- **WHEN** the VM is gone
- **THEN** the investor can restore the latest uploaded snapshot onto a new host by following the README

#### Scenario: Restore
- **WHEN** the investor follows the README's restore steps with a snapshot
- **THEN** the bot starts with the data from that snapshot
