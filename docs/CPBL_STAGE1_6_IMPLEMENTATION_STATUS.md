# CPBL Trackman expansion — Stage 1–6 implementation status

Status: implementation complete on `feature/cpbl-dataset`; Stage 7 full-season acceptance remains separate.

## Design invariant

MLB/Baseball Savant and CPBL/Trackman are independent datasets. They share the relational/numerical analysis implementation but do not share raw storage, normalized databases, analytical mirrors, cache/history databases, or runtime dataset context.

A process is pinned to one dataset with `--dataset mlb` or `--dataset cpbl`.

## Physical layout

Existing MLB installations keep their current paths for backward compatibility:

```text
data/
├── statcast.sqlite3
├── statcast.duckdb
├── analysis_state.sqlite3
└── raw/...
```

CPBL is isolated below its own dataset root:

```text
data/cpbl/
├── cpbl.sqlite3
├── cpbl.duckdb
├── analysis_state.sqlite3
└── raw/
    ├── schedule/...
    └── games/...
```

No implicit cross-league union or cross-dataset cache lookup exists.

## Stage 1 — source contract

Implemented in `docs/CPBL_SOURCE_CONTRACT.md`.

The pitch acquisition path is:

```text
calendar date
  -> /api/proxy/v1/games/schedule/<YYYY-MM-DD>
  -> GameId
  -> /api/proxy/v1/games/<GameId>
  -> LiveLog[]
  -> per-pitch Trackman payload
```

Contract decisions:

- completed games without Trackman are valid source records, not sync failures;
- identifiable play-by-play pitches are retained even when an individual pitch has no Trackman measurements;
- missing measurements remain NULL;
- raw game JSON is archived before normalization;
- `AutoPitchType`, `TaggedPitchType`, and `PitchCall` are retained verbatim;
- deterministic canonical PA/pitch ordering is generated for the shared analysis contract;
- full-season field/value coverage enumeration is Stage 7 acceptance work, not a prerequisite for Stage 1–6 implementation.

A live integration smoke test covers a known 2026 schedule/game pair so an upstream API contract change is visible in CI.

## Stage 2 — isolated CPBL data layer

Implemented modules:

- `cpbl_client.py` — retrying client for the public CPBL proxy API;
- `cpbl_raw.py` — gzip JSON raw archive with SHA-256 manifests and verified reads;
- `cpbl_normalize.py` — deterministic pitch-grain normalization;
- `cpbl_sync.py` — date/game backfill, update, retry, scheduler and raw rebuild;
- shared `StatcastStore`/DuckDB mirror machinery is reused against the isolated CPBL database.

Normalized CPBL rows include the shared canonical analysis fields plus provider-native Trackman fields.

Important native fields include:

- `pitch_call`
- `auto_pitch_type`
- `tagged_pitch_type`
- `rel_speed_kph`
- `spin_rate`
- `extension_m`
- `rel_height_m`
- `rel_side_m`
- `zone_speed_kph`
- `horz_appr_angle`
- `vert_appr_angle`
- trajectory coefficient JSON
- available batted-ball/contact/landing measurements
- `cpbl_has_trackman` coverage flag

Reingesting an identical raw game is idempotent through the existing natural pitch identity / row hash machinery.

## Stage 3 — dataset architecture separation

`datasets.py` provides a `DatasetSpec` and AppConfig-compatible `DatasetConfigView`.

The selected dataset owns:

- normalized SQLite path;
- DuckDB analytical mirror path;
- analysis state/cache/history/saved-analysis path;
- raw root;
- earliest supported date;
- refresh policy;
- provider/unit metadata.

`dataset_webapp.py` binds existing application services to one dataset. Existing MLB paths and behavior remain the default, so the CPBL feature does not require migrating an existing Savant installation.

## Stage 4 — CPBL semantic layer

`cpbl_semantics.py` maps known Trackman classifications into the canonical vocabulary used by the shared analysis modes while retaining provider-native values.

Examples:

```text
FourSeamFastBall -> FF
Slider           -> SL
ChangeUp         -> CH
StrikeCalled     -> called_strike
StrikeSwinging   -> swinging_strike
BallCalled       -> ball
InPlay           -> hit_into_play
```

Unknown provider values are preserved rather than discarded. They remain queryable through native fields and can be classified later without refetching raw data.

The CPBL metadata contract reports kph for canonical speed values and metres for provider distance values. MLB continues to report its own existing units. Datasets are never mixed implicitly, so no hidden cross-league unit conversion occurs.

## Stage 5 — shared analysis functionality on CPBL

The existing analysis engine remains single-source. CPBL known-answer tests exercise the existing modes against canonical CPBL fields and native Trackman measurements:

- Basic analysis
- Sequence Pattern
- Follow Event
- Arsenal
- Pitch Role
- Temporal
- Percentile
- Cross-Level
- Arsenal Change
- Workflow
- Clustering
- Regression
- Bootstrap
- Cluster Compare

Numerical CPBL tests explicitly use Trackman HAA/VAA and release speed/spin rather than Savant-only fields.

An end-to-end equivalence test builds the CPBL SQLite source database, builds the CPBL DuckDB mirror, submits the same analysis request to both backends, and requires identical analytical results.

## Stage 6 — CPBL workspace/UI

CLI selection:

```bash
treepolo-mlb --dataset cpbl init
treepolo-mlb --dataset cpbl backfill --start 2026-03-01 --end 2026-09-15 --resume
treepolo-mlb --dataset cpbl update
treepolo-mlb --dataset cpbl analytics-sync
treepolo-mlb --dataset cpbl status
treepolo-mlb --dataset cpbl verify
treepolo-mlb --dataset cpbl ui
```

The same frontend component system is reused, but `/api/meta` declares the active dataset and units. `dataset-workspace.js` updates the product title and adds a visible workspace badge.

Because field legality is schema/capability-driven, native numeric Trackman fields become available to compatible filters/workflows/numerical controls without a CPBL-specific copy of every frontend panel.

MLB-only supplemental Pitch3D/Hawk-Eye controls are not loaded in the CPBL workspace. The server rejects MLB supplemental-data actions from a CPBL process as a second boundary.

Cache, history, saved analyses and DuckDB acceleration operate against the CPBL-specific files because the entire server process is bound to the CPBL dataset spec.

## Automated acceptance added in Stage 1–6

CPBL-specific tests cover:

- deterministic normalization;
- native and canonical pitch classification values;
- numeric Trackman field typing;
- missing-Trackman pitch retention;
- zero-pitch games with no advanced data;
- idempotent ingestion;
- physical MLB/CPBL path isolation;
- runtime dataset metadata and units;
- raw schedule/game archiving;
- fast-status updates;
- SQLite/DuckDB analytical equivalence;
- all existing analysis mode families listed above;
- CPBL UI identity and MLB-only supplemental gating;
- live CPBL schedule/game API contract smoke.

Existing MLB tests remain in the same suite and must continue to pass.

## Stage 7 intentionally remaining

Stage 7 is release/full-season acceptance rather than missing Stage 1–6 product functionality. It will include:

- complete 2026 season acquisition at production scale;
- exhaustive observed `AutoPitchType`, `TaggedPitchType`, `PitchCall` enumeration;
- field-by-field Trackman coverage/missingness report;
- game/pitch count reconciliation against the source site;
- full-season raw rebuild recovery test;
- large DuckDB mirror build/incremental-refresh timing;
- large workflow/numerical analysis stress tests;
- explicit source-unavailable field report;
- final CPBL acceptance report.

Until Stage 7 is run, Stage 1–6 should be described as feature-complete implementation with automated functional coverage, not as full-season production acceptance.
