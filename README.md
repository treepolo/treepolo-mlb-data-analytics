# treepolo MLB Data Analytics

Baseball Savant/Statcast pitch-level data foundation for later fine-grained MLB analysis. This repository currently implements the complete ingestion/synchronization layer; analytical UI is intentionally out of scope for this stage.

## What is implemented

- Historical backfill from 2015 onward, split into bounded requests to avoid oversized Savant queries.
- Exact compressed raw-response archive for every successful request.
- Normalized local SQLite database optimized for later filtering/sequence analysis.
- Full upstream-column preservation with automatic schema evolution when Savant adds fields.
- Stable pitch identity (`game_pk:at_bat_number:pitch_number`) with fallback identity diagnostics.
- Idempotent upserts: re-fetches update corrected Statcast values without duplicating pitches.
- Incremental update plus configurable recent-day re-fetch window for post-game Statcast corrections.
- Automatic update setting, manual update, scheduler entry point, backfill and rebuild kept separate.
- Retry/backoff, resumable backfill behavior, per-run and per-chunk error/state records.
- Integrity report: pitch rows, games, duplicates, missing natural keys, latest date, failed chunks, schema drift and raw snapshot count.
- Rebuild normalized storage entirely from archived raw snapshots.
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

## Commands

```bash
# Full historical import (default 2015-01-01 through today)
treepolo-mlb backfill

# Explicit range
treepolo-mlb backfill --start 2015-01-01 --end 2026-08-23

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

Backfill continues after failed chunks by default and records every failure. `--fail-fast` changes that behavior. After a partial run, simply run the range again; idempotent upserts make retry safe.

## Tests

```bash
pytest -q -m "not integration"
pytest -q -m integration  # requires internet access to Baseball Savant
```

CI runs both the deterministic test suite and a live one-day Savant smoke test. The live test checks that the endpoint returns pitch-level rows and core fields including spin rate, 2D spin axis, movement, pitch result and batted-ball metrics.
