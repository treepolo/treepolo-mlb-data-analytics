# treepolo MLB Data Analytics

Baseball Savant/Statcast pitch-level data foundation and local analysis application. The project includes ingestion/synchronization, a typed relational analysis engine, advanced sequence/arsenal workflows, a typed numerical-analysis boundary, a SQLite source-of-truth database, a DuckDB analytical mirror, persistent analysis state, and a local bilingual frontend.

## Project plan

The canonical long-term architecture, ten stress-test analysis requirements, Stage 4 status, and future backlog are documented in [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md). Important future work should be recorded there rather than existing only in chat history.

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
- Typed analysis tree with filtering, aggregation, ranking, explicit window frames, joins, set operations and serialization.
- Ordered plate-appearance event patterns, bounded follow-up events, pitch-usage/arsenal analysis, relative pitch-role ranking, individual percentile thresholds, temporal comparisons and cross-level comparisons.
- Research Workflow composition: grouped/conditional metrics, derived arithmetic, filters, rolling windows, lag/lead, consecutive trends, nth/first/last selection, within-group ranking, projection and sorting.
- Basic statistics including count, average, min/max, sum, median, population SD and sample SD.
- Typed Numerical Executor consuming an explicit relational result with preserved schema/grain.
- K-means and Gaussian Mixture clustering, including per-entity independent fitting and deterministic seeds.
- Linear and binary logistic regression.
- Bootstrap confidence intervals with an explicit resampling unit instead of silently treating correlated pitch rows as independent.
- Full multi-stage cluster comparison path for stress test #10: arsenal-group candidate selection → per-entity clustering → best-cluster selection → reference-pitch comparison.
- Persistent DuckDB columnar analytical mirror with SQLite fallback for large relational analytical queries.
- SQLite analysis indexes plus `ANALYZE` / `PRAGMA optimize` support.
- Persistent result cache keyed by canonical analysis payload + data revision + requested backend; changed Statcast data therefore invalidates old cache keys automatically.
- Persistent analysis history plus saved-analysis definitions and cached-result restoration when available.
- One shared field-checklist renderer for the original eight multi-select controls.
- One shared result-ordering component and backend ordering layer for the original nine relational analysis modes.
- One shared analysis Job/Progress system used by relational and Stage 4 analysis execution.
- Reproducible local performance benchmark for the canonical season/pitch-type average-velocity query.
- Local Chinese/English frontend with Windows XP/Windows 7 desktop-application visual style.
- Four general Stage 4 research pages: Research Workflow, Clustering, Regression and Bootstrap, plus the dedicated Multi-stage Cluster Comparison page.
- Table-only analysis results in the current frontend stage; charting/export remain intentionally deferred to the next product layer.
- Unit/integration tests, known-answer numerical tests, stress-test acceptance coverage, and a live Baseball Savant smoke test in GitHub Actions.

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
treepolo-mlb init
```

DuckDB, NumPy, SciPy and scikit-learn are normal project dependencies. After pulling a revision that changes dependencies, run the editable install command again.

`init` creates `config.json` and the local databases under `data/`. The `data/` directory is intentionally gitignored; Statcast data, analytical mirrors, and analysis-state caches should not be committed to Git.

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

The frontend keeps Chinese and English visible together. It exposes user-meaningful analysis concepts while keeping low-level SQL joins/window implementation details out of the ordinary interface.

The original relational pages share result-ordering controls. All analysis execution uses the common progress system: DuckDB queries can expose actual query progress; SQLite fallback reports stage/elapsed status instead of inventing a percentage; numerical execution reports real computation stages. Analysis Library stores history and reusable analysis definitions across browser/UI restarts.

Stage 4 advanced research pages allow relational preparation before numerical work. For example, rows can first be grouped or ranked, then passed into clustering/regression/bootstrap without bypassing the typed grain contract.

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

Long-running CLI maintenance commands expose stage/current-work/elapsed progress where a trustworthy exact percentage is unavailable rather than displaying a fake percentage.

## Storage model

`data/raw/**` contains gzip-compressed exact Savant CSV responses plus manifests/checksums.

`data/statcast.sqlite3` is the authoritative normalized pitch database and synchronization metadata store. Every valid upstream CSV header becomes a database column automatically. Unknown future Savant fields are preserved and logged as schema events instead of being dropped.

`data/statcast.duckdb` is a persistent columnar analytical mirror. It exists for fast analytical scans/grouping/window workloads; it is not the source of truth. If it is missing or stale, it can be rebuilt/refreshed from SQLite. If DuckDB execution fails, relational analysis can fall back to SQLite.

`data/analysis_state.sqlite3` stores result-cache entries, analysis history and saved analysis definitions. It is deliberately separate from Statcast source-of-truth data. Cache keys include the Statcast `data_revision`, so a data refresh does not silently reuse a result computed from an older dataset.

The primary pitch identity is `game_pk + at_bat_number + pitch_number`. Rows missing any part of that natural key receive a deterministic fallback key and are surfaced by `verify`.

## Analysis architecture

The main execution flow is:

```text
Frontend / saved analysis definition
        ↓
Typed Analysis AST / Research Workflow
        ↓
Execution Planner
        ├─ DuckDB relational executor (primary)
        └─ SQLite relational executor (fallback)
        ↓
Typed relational result (columns + rows + grain)
        ↓ optional
Numerical Executor
        ├─ clustering
        ├─ regression
        └─ bootstrap
        ↓
Structured result sections
```

DuckDB remains a relational analytical executor; it is not the Numerical Executor. Numerical methods only receive an explicit typed relational result, and clustering assignments preserve grain keys so their labels can be safely related back to the analysis units that produced them.

For clustering, `partition_fields` can request separate model fitting inside each entity (for example, one model per pitcher) instead of mixing all entities into one global clustering problem.

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

The report separates DuckDB mirror preparation from measured query runs and reports min/median/max query time. The Stage 4 roadmap records the completed full persistent-database benchmark; CI synthetic data remains a correctness check rather than a substitute for local multi-million-row performance measurement.

`treepolo-mlb optimize` remains useful for SQLite fallback and comparison. The project intentionally does not create indexes for every possible combination of Statcast columns; general OLAP work is primarily routed through DuckDB.

## Statistical safety behavior

- Granular analysis results retain sample size instead of presenting fine-grained rates without context.
- Numerical input drops rows that are NULL/non-numeric only where the selected numerical method requires complete numeric features and reports the number of complete rows used.
- Clustering has an explicit maximum-input safety threshold; it refuses an oversized full input instead of silently truncating it and changing the statistical population.
- Per-entity clustering fits independent models inside the requested partitions.
- Regression reports model sample size and model-specific diagnostics.
- Bootstrap requires explicit resampling-unit fields and supports grouped differences, preventing the API from silently assuming every pitch is an independent experimental unit.
- Randomized numerical methods expose reproducible seeds.

## Data correctness behavior

Daily updates intentionally overlap recent dates. If Savant revises velocity, pitch classification, batted-ball values, or other fields after a game, the same pitch is updated in place. Re-running the same range is therefore safe and does not inflate the database.

Backfill continues after failed chunks by default and records every failure. `--fail-fast` changes that behavior. `--resume` skips exact date chunks already completed successfully, while `retry-failed` re-runs recorded failed chunks. Idempotent upserts make all of these retries safe.

After successful data maintenance, an existing DuckDB mirror is refreshed best-effort. A mirror-refresh problem does not turn a successful SQLite ingest into a failed data update.

## Configuration

`config.json` contains the data directory, SQLite database name, DuckDB analytical database name, analysis-state database name, default analysis backend, earliest backfill date, Savant request chunk size, correction window, retries/backoff, pacing and automatic-update interval.

Existing configuration files that omit newer fields continue to receive application defaults.

## Tests

```bash
pytest -q -m "not integration"
pytest -q -m integration  # requires internet access to Baseball Savant
```

CI runs both the deterministic suite and a live Savant smoke test. Deterministic coverage includes shared UI behavior, SQLite-vs-DuckDB relational compatibility, workflow composition, known-answer clustering/regression/bootstrap behavior, grain preservation, cache/history persistence, and the multi-stage stress-test #10 acceptance path.
