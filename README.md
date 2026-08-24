# treepolo MLB Data Analytics

Baseball Savant/Statcast pitch-level data foundation and local analysis application. The project includes the ingestion/synchronization system, typed analysis engine, advanced sequence/arsenal analysis capabilities, and a deliberately simple local frontend for operating those capabilities and viewing returned tables.

## What is implemented

- Historical backfill from 2015 onward, split into bounded requests to avoid oversized Savant queries.
- Exact compressed raw-response archive for every successful request.
- Normalized local SQLite database optimized for filtering and sequence analysis.
- Full upstream-column preservation with automatic schema evolution when Savant adds fields.
- Stable pitch identity (`game_pk:at_bat_number:pitch_number`) with fallback identity diagnostics.
- Idempotent upserts: re-fetches update corrected Statcast values without duplicating pitches.
- Incremental update plus configurable recent-day re-fetch window for post-game Statcast corrections.
- Automatic update setting, manual update, scheduler entry point, backfill and rebuild kept separate.
- Retry/backoff, resumable backfill behavior, per-run and per-chunk error/state records.
- Integrity report: pitch rows, games, duplicates, missing natural keys, latest date, failed chunks, schema drift and raw snapshot count.
- Rebuild normalized storage entirely from archived raw snapshots.
- Typed analysis tree with filtering, aggregation, ranking, window operations, joins, set operations and serialization.
- Ordered plate-appearance event patterns, bounded follow-up events, pitch-usage/arsenal analysis, relative pitch-role ranking, individual percentile thresholds, temporal comparisons and cross-level comparisons.
- Local bilingual Chinese/English frontend with a Windows XP/Windows 7 desktop-application visual style.
- Frontend data-management controls for status, update, auto update, backfill, failed-chunk retry and rebuild.
- Table-only analysis results in the current frontend stage; charting and additional frontend-only analysis features are intentionally not included yet.
- Unit/integration tests and a live Baseball Savant smoke test in GitHub Actions.

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
treepolo-mlb init
```

`init` creates `config.json` and the local database under `data/`. The `data/` directory is intentionally gitignored; Statcast data should not be committed to Git.

## Local frontend

Start the frontend with:

```bash
treepolo-mlb ui
```

By default it binds only to `127.0.0.1:8765` and opens the default browser. The frontend and its API therefore stay local to the machine unless a different host is explicitly supplied.

Optional examples:

```bash
# Use another local port
treepolo-mlb ui --port 9000

# Start without opening the browser automatically
treepolo-mlb ui --no-browser
```

The frontend keeps Chinese and English visible together rather than providing a language switch. It exposes user-meaningful analysis concepts while keeping low-level execution concepts such as SQL joins and window implementation details out of the interface. Current results are deliberately shown as tables only.

If Auto Update is enabled, the existing scheduler logic runs while the UI service is running. For a machine-level recurring schedule independent of the UI process, use the scheduler command with Windows Task Scheduler, cron, a service, or an equivalent wrapper.

## Data commands

```bash
# Full historical import (default 2015-01-01 through today)
treepolo-mlb backfill

# Explicit range
treepolo-mlb backfill --start 2015-01-01 --end 2026-08-23

# Resume a previously attempted backfill and skip exact chunks already completed
treepolo-mlb backfill --start 2015-01-01 --end 2026-08-23 --resume

# Retry chunks recorded as failed
treepolo-mlb retry-failed

# Incremental sync; also re-fetches the recent correction window
treepolo-mlb update

# Data-quality and sync status
treepolo-mlb verify

# Automatic-update switch
treepolo-mlb auto-update --enable
treepolo-mlb auto-update --disable

# Scheduler process; suitable for a service/container/Task Scheduler wrapper
treepolo-mlb scheduler

# One scheduler iteration (useful from cron/Windows Task Scheduler)
treepolo-mlb scheduler --once

# Recreate normalized DB from preserved raw responses
treepolo-mlb rebuild --yes
```

## Configuration

`config.json` contains the data directory, earliest backfill date, Savant request chunk size, recent correction window, retries/backoff, request pacing, and automatic-update interval. Defaults are conservative: five-day backfill chunks, seven-day correction refresh, bounded retry with exponential backoff, and automatic updates disabled until explicitly enabled.

## Storage model

`data/raw/**` contains gzip-compressed exact Savant CSV responses plus manifests/checksums. `data/statcast.sqlite3` contains normalized pitch rows and synchronization metadata. Every valid upstream CSV header becomes a database column automatically. Unknown future Savant fields are preserved and logged as schema events instead of being dropped.

The primary pitch identity is `game_pk + at_bat_number + pitch_number`. Rows missing any part of that natural key receive a deterministic fallback key and are surfaced by `verify`; this prevents silent data loss while making malformed upstream records visible.

## Data correctness behavior

Daily updates intentionally overlap recent dates. If Savant revises velocity, pitch classification, batted-ball values, or other fields after a game, the same pitch is updated in place. Re-running the same range is therefore safe and does not inflate the database.

Backfill continues after failed chunks by default and records every failure. `--fail-fast` changes that behavior. `--resume` skips exact date chunks already completed successfully, while `retry-failed` re-runs recorded failed chunks. Idempotent upserts make all of these retries safe.

## Tests

```bash
pytest -q -m "not integration"
pytest -q -m integration  # requires internet access to Baseball Savant
```

CI runs both the deterministic test suite and a live one-day Savant smoke test. The live test checks that the endpoint returns pitch-level rows and core fields including spin rate, 2D spin axis, movement, pitch result and batted-ball metrics.
