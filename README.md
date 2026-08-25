# treepolo MLB Data Analytics

Baseball Savant/Statcast pitch-level data foundation and local analysis application. The project includes the ingestion/synchronization system, typed analysis engine, advanced sequence/arsenal analysis capabilities, a SQLite source-of-truth database, a DuckDB analytical mirror, and a local bilingual frontend.

## Project plan

The canonical long-term architecture, ten stress-test analysis requirements, Stage 4 plan, and future backlog are documented in [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md). Important future work should be recorded there rather than existing only in chat history.

## What is implemented

- Historical backfill from 2015 onward, split into bounded Savant requests.
- Exact gzip-compressed raw-response archive for every successful request.
- Normalized local SQLite database used as the authoritative data store.
- Full upstream-column preservation with automatic schema evolution when Savant adds fields.
- Stable pitch identity (`game_pk:at_bat_number:pitch_number`) with fallback identity diagnostics.
- Idempotent upserts: re-fetches update corrected Statcast values without duplicating pitches.
- Incremental update plus configurable recent-day re-fetch window for post-game Statcast corrections.
- Automatic update setting, manual update, scheduler entry point, backfill and rebuild kept separate.
- Retry/backoff, resumable backfill behavior, per-run and per-chunk error/state records.
- Persistent fast status cache so ordinary UI startup does not rescan the multi-million-row pitch table.
- Integrity report and rebuild from preserved raw snapshots.
- Typed analysis tree with filtering, aggregation, ranking, window operations, joins, set operations and serialization.
- Ordered plate-appearance event patterns, bounded follow-up events, pitch-usage/arsenal analysis, relative pitch-role ranking, individual percentile thresholds, temporal comparisons and cross-level comparisons.
- Basic statistics including count, average, min/max, sum, median, population SD and sample SD.
- Persistent DuckDB columnar analytical mirror with SQLite fallback for large analytical queries.
- SQLite analysis indexes plus `ANALYZE` / `PRAGMA optimize` support.
- One shared field-checklist renderer for all eight current multi-select analysis controls.
- One shared result-ordering component and backend ordering layer for all nine analysis modes.
- One shared analysis Job/Progress system for all analysis modes.
- Reproducible local performance benchmark for the canonical season/pitch-type average-velocity query.
- Local Chinese/English frontend with Windows XP/Windows 7 desktop-application visual style.
- Table-only analysis results in the current frontend stage; charting remains intentionally deferred.
- Unit/integration tests and a live Baseball Savant smoke test in GitHub Actions.

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
treepolo-mlb init
```

`duckdb` is a normal project dependency. After pulling a revision that changes dependencies, run the editable install command again.

`init` creates `config.json` and the local database under `data/`. The `data/` directory is intentionally gitignored; Statcast data and the analytical mirror should not be committed to Git.

## Local frontend

Start the frontend with:

```bash
treepolo-mlb ui
```

By default it binds only to `127.0.0.1:8765` and opens the default browser.

Optional examples:

```bash
# Use another local port
treepolo-mlb ui --port 9000

# Start without opening the browser automatically
treepolo-mlb ui --no-browser
```

The frontend keeps Chinese and English visible together. It exposes user-meaningful analysis concepts while keeping low-level SQL joins/window implementation details out of the interface.

All nine analysis modes share the same result-ordering controls and analysis-progress system. DuckDB queries can expose actual query progress; SQLite fallback reports stage/elapsed status instead of inventing a percentage.

If Auto Update is enabled, the scheduler logic runs while the UI service is running. For a machine-level recurring schedule independent of the UI process, use the scheduler command with Windows Task Scheduler, cron, a service, or equivalent wrapper.

## Data commands

```bash
# Full historical import (default 2015-01-01 through today)
treepolo-mlb backfill

# Explicit range
treepolo-mlb backfill --start 2015-01-01 --end 2026-08-25

# Resume and skip exact chunks already completed successfully
treepolo-mlb backfill --start 2015-01-01 --end 2026-08-25 --resume

# Retry chunks recorded as failed
treepolo-mlb retry-failed

# Incremental sync; also re-fetches the recent correction window
treepolo-mlb update

# Fast status / full integrity check
treepolo-mlb status
treepolo-mlb verify

# Build/refresh SQLite analysis indexes and planner statistics
treepolo-mlb optimize

# Build or refresh the persistent DuckDB analytical mirror
treepolo-mlb analytics-sync

# Compare the representative query on SQLite and DuckDB
treepolo-mlb benchmark --year 2026 --runs 3 --backend both

# Automatic-update switch
treepolo-mlb auto-update --enable
treepolo-mlb auto-update --disable

# Scheduler process / one iteration
treepolo-mlb scheduler
treepolo-mlb scheduler --once

# Recreate normalized DB from preserved raw responses
treepolo-mlb rebuild --yes
```

## Storage model

`data/raw/**` contains gzip-compressed exact Savant CSV responses plus manifests/checksums.

`data/statcast.sqlite3` is the authoritative normalized pitch database and synchronization metadata store. Every valid upstream CSV header becomes a database column automatically. Unknown future Savant fields are preserved and logged as schema events instead of being dropped.

`data/statcast.duckdb` is a persistent columnar analytical mirror. It exists for fast analytical scans/grouping/window workloads; it is not the source of truth. If it is missing or stale, it can be rebuilt/refreshed from SQLite. If DuckDB execution fails, analysis can fall back to SQLite.

The primary pitch identity is `game_pk + at_bat_number + pitch_number`. Rows missing any part of that natural key receive a deterministic fallback key and are surfaced by `verify`.

## Performance workflow

The canonical benchmark corresponds to a real interactive analysis:

```text
Season = 2026
Group By = pitch_type
Metrics = Count + Average release_speed
Sort = Average release_speed descending
```

Run:

```bash
treepolo-mlb benchmark --year 2026 --runs 3 --backend both
```

The report separates DuckDB mirror preparation from measured query runs and reports min/median/max query time. Full-database performance should be validated on the actual persistent 2015-present dataset; CI synthetic data cannot substitute for that measurement.

`treepolo-mlb optimize` remains useful for SQLite fallback and comparison. The project intentionally does not create indexes for every possible combination of Statcast columns; general OLAP work is primarily routed through DuckDB.

## Data correctness behavior

Daily updates intentionally overlap recent dates. If Savant revises velocity, pitch classification, batted-ball values, or other fields after a game, the same pitch is updated in place. Re-running the same range is therefore safe and does not inflate the database.

Backfill continues after failed chunks by default and records every failure. `--fail-fast` changes that behavior. `--resume` skips exact date chunks already completed successfully, while `retry-failed` re-runs recorded failed chunks. Idempotent upserts make all of these retries safe.

After successful data maintenance, an existing DuckDB mirror is refreshed best-effort. A mirror-refresh problem does not turn a successful SQLite ingest into a failed data update.

## Configuration

`config.json` contains the data directory, SQLite database name, DuckDB analytical database name, default analysis backend, earliest backfill date, Savant request chunk size, correction window, retries/backoff, pacing and automatic-update interval.

Existing configuration files that omit the newer DuckDB fields continue to receive the application defaults.

## Tests

```bash
pytest -q -m "not integration"
pytest -q -m integration  # requires internet access to Baseball Savant
```

CI runs both the deterministic test suite and a live Savant smoke test. Deterministic coverage includes shared sorting/checklist/progress behavior and SQLite-vs-DuckDB result compatibility for representative basic, sequence and arsenal analysis paths.
