# CPBL Stage 7 — Production Acceptance Plan

Status: planned / not yet executed

Branch: `feature/cpbl-dataset`

Stage 7 is the final production-acceptance phase for the CPBL/Trackman dataset. It is intentionally separate from Stage 1–6 implementation. Stage 7 does not exist to add another layer of features; it exists to prove that the completed CPBL implementation is complete, reproducible, performant, and usable against the real public 2026 CPBL dataset.

The final Stage 7 closure criterion is strict: CPBL is not considered fully complete until both the engineering/data acceptance track and the real-user acceptance track pass.

---

## 1. Acceptance tracks

Stage 7 has two parallel tracks.

### Track A — Engineering / data acceptance

Prove that the full public CPBL dataset can be acquired, reconciled, rebuilt, incrementally updated, mirrored to DuckDB, and queried reliably without unexplained data loss or cross-dataset contamination.

### Track B — Real-user acceptance

Run the original ten permanent architecture stress tests through the actual CPBL workspace using real CPBL data and the real UI/workflow. Passing backend unit tests alone is insufficient.

The original ten questions are preserved as permanent regression / acceptance requirements from `docs/STAGE4_ACCEPTANCE_REPORT.md`.

---

## 2. Stage 7 subphases

### 7A — Full-season acquisition

Start from a clean CPBL dataset root and acquire all currently completed, publicly available 2026 CPBL games.

Acquisition path:

```text
calendar date
  -> schedule endpoint
  -> GameId
  -> game JSON
  -> LiveLog
  -> identifiable pitches
  -> Trackman payload when present
  -> normalized SQLite pitches
```

Required checks:

- enumerate every relevant date in the supported 2026 season window;
- record every completed game visible through the public source;
- archive schedule JSON and game JSON before normalization;
- distinguish completed games with no Trackman data from synchronization failures;
- retain identifiable pitches even when a particular pitch has no Trackman measurement;
- no silent game omission;
- no silent pitch omission;
- no duplicate normalized pitch identity on repeated ingest.

Pass condition:

Every completed source game has a deterministic final state: successfully mirrored, intentionally Trackman-unavailable, or explicitly failed with a recorded reason. There must be no unknown/untracked omissions.

---

### 7B — Source reconciliation

Produce a per-game reconciliation table with at least:

```text
game_id
source_date
source_status
raw_schedule_present
raw_game_present
live_log_entries
identifiable_pitches
normalized_pitches
trackman_pitches
missing_trackman_pitches
duplicate_pitch_uid
unknown_auto_pitch_type
unknown_tagged_pitch_type
unknown_pitch_call
```

Core invariant:

```text
identifiable source pitches == normalized pitches
```

except for a difference that is explicitly understood, documented, and accepted as a source-contract exception.

Example acceptable case:

```text
identifiable pitches = 298
normalized pitches   = 298
Trackman pitches     = 296
```

Two pitches lacked measurements but were retained.

Example blocking failure:

```text
identifiable pitches = 298
normalized pitches   = 296
```

unless the two-row difference can be traced to a documented source-contract rule.

Also reconcile season-level totals:

- schedule game count;
- raw game snapshot count;
- completed game count;
- normalized pitch count;
- Trackman pitch count;
- pitches retained without Trackman;
- duplicate pitch identities;
- failed game requests.

---

### 7C — Full field / semantic census

Walk the entire acquired raw game corpus and enumerate every observed provider field and semantic value.

Enumerate complete observed value sets for at least:

- `AutoPitchType`;
- `TaggedPitchType`;
- `PitchCall`;
- game status / game kind values relevant to normalization;
- any provider enum consumed by the normalizer or semantic layer.

For every Trackman/native numeric field, report:

- observed type(s);
- non-NULL count;
- NULL count and missingness rate;
- minimum;
- maximum;
- representative values;
- malformed / nonnumeric observations where a numeric type is expected.

Important fields include, where publicly present:

- release speed;
- spin rate;
- extension;
- release height / side;
- plate location;
- zone speed;
- HAA / VAA;
- trajectory coefficients;
- batted-ball speed / angle / direction;
- contact coordinates;
- hit spin;
- landing / hang-time fields.

Requirements:

- every native provider value is preserved even if it is not yet mapped to a canonical value;
- unknown canonical mappings are reported, not dropped;
- no Savant-only field is fabricated to make CPBL look schema-compatible;
- provider-native and canonical values remain traceable to each other.

Produce a source-unavailable report for measurements CPBL simply does not publish.

---

### 7D — Sync lifecycle / disaster recovery

Exercise the full CPBL data-management lifecycle against real data.

Required scenarios:

1. clean full backfill;
2. intentional interruption during backfill;
3. resume from the interrupted state;
4. rerun an already completed date range;
5. overlapping backfill/update windows;
6. normal incremental update;
7. repeat the same update with no new source data;
8. retry recorded failures;
9. scheduler/single-cycle update path;
10. verify raw archive hashes/manifests;
11. delete/recreate the normalized CPBL SQLite database from raw snapshots;
12. rebuild and compare against the pre-rebuild database.

Pass conditions:

- repeated ingestion is idempotent;
- no duplicate pitch rows are introduced;
- deterministic raw input produces deterministic normalized pitch identities;
- rebuild restores an analytically equivalent dataset;
- fast status and data revision remain coherent;
- failures are resumable and observable;
- raw archive corruption/missing snapshot verification is surfaced explicitly.

Compare before vs after rebuild:

```text
pitch count
pitch_uid set
important canonical/native values
schema
data revision behavior
representative analysis outputs
```

---

### 7E — SQLite / DuckDB analytical correctness

Build the full CPBL DuckDB analytical mirror and validate both full-build and incremental-refresh behavior.

Run representative real-data queries against both backends covering:

- filtering;
- aggregate / conditional aggregate;
- derived arithmetic;
- rank;
- rolling windows;
- lag / lead;
- consecutive trend;
- event pattern;
- follow event;
- arsenal signature;
- relative pitch selection / annotation;
- empirical percentile;
- arsenal change;
- cross-level comparison;
- workflow chains.

For identical analysis payloads compare:

- section names;
- columns;
- row counts;
- row values;
- NULL semantics;
- explicitly requested ordering;
- floating-point values within justified numerical tolerance only.

Also verify:

- full DuckDB mirror construction;
- no-op refresh;
- refresh after new CPBL rows arrive;
- refresh after changed rows where supported;
- schema evolution behavior;
- cache/data-revision invalidation after dataset change.

Any semantic SQLite/DuckDB disagreement is blocking.

---

### 7F — Full-data performance / stability

Measure both cold and warm behavior on the full real CPBL dataset.

Benchmark at least:

- application/UI startup;
- metadata/schema loading;
- Basic Analysis;
- ordinary Research Workflow;
- Sequence Pattern;
- Follow Event;
- Arsenal / Pitch Role;
- large-result table rendering;
- paging;
- cached result reload;
- History reload;
- Saved Analysis reload;
- Arsenal Change;
- Cross-Level Comparison;
- Clustering;
- Regression;
- Bootstrap;
- Cluster Comparison;
- first DuckDB build;
- incremental DuckDB refresh.

Record durations, result sizes, backend, cold/warm state, and any material memory behavior.

Product-level blocking failures include:

- UI or ordinary analysis appearing frozen for unreasonable periods;
- large-result paging freezing the interface;
- stale/blank result races;
- cache reload being materially worse than recomputation without explanation;
- progress UI not advancing for heavy operations;
- unbounded memory growth under ordinary repeated use;
- analysis failure solely because a single low-sample entity cannot satisfy a numerical model requirement when the contract says it should be skipped.

Machine-dependent timings should be recorded as benchmarks rather than encoded as fragile millisecond assertions unless a stable regression threshold is justified.

---

## 3. Stage 7G — Original ten real-user acceptance tests on CPBL

The original ten architecture stress tests remain permanent acceptance requirements. They must be run through the real CPBL workspace with real full-season data, not only through fixtures or backend unit tests.

Each test should include:

- actual UI setup;
- successful execution;
- result inspection;
- at least one raw-data spot-check where applicable;
- correctness judgment;
- usability / wording / field-selector observations;
- performance observation;
- SQLite/DuckDB comparison where practical.

### #1 — Exact three target pitches: all-consecutive vs none-adjacent

Original structure:

> Same plate appearance contains exactly three Sweepers; last pitch is also a Sweeper; compare all three consecutive vs completely non-adjacent; analyze only the third Sweeper.

Acceptance points:

- PA partitioning is correct;
- exact event count = 3;
- occurrence = third event;
- final-pitch requirement works;
- all-consecutive cohort is correct;
- none-adjacent cohort is correct;
- partially adjacent cases are excluded;
- Event Pattern Cohorts can continue into downstream workflow;
- manually inspect several complete plate appearances.

Provider adaptation rule:

Use Sweeper if CPBL's observed classification has adequate Sweeper samples. Otherwise choose a sufficiently common real CPBL breaking-ball class while preserving the exact event semantics. Do not weaken the test.

### #2 — Arsenal signature + usage role + same-arsenal cohort comparison

Acceptance points:

- minimum usage correctly determines arsenal membership;
- arsenal signatures are deterministic;
- pitch usage ranking occurs within entity;
- ties follow configured semantics;
- same-arsenal pitchers can form a cohort;
- relative pitch selector works downstream;
- same workflow can continue to comparison/aggregate steps.

### #3 — Three-game rising pitch usage -> fourth-game metric

Build pitcher × game × pitch-type usage, identify three consecutive games with strictly rising usage, then use Lead to retrieve the fourth game's metric.

Acceptance points:

- per-game usage denominator is correct;
- period ordering is correct;
- sequences do not cross pitcher/pitch-type boundaries;
- strict rising logic is correct;
- fourth game is actually the immediately following game in the ordered partition;
- manually recompute at least one real pitcher/pitch-type example.

Outcome metric should use a measurement CPBL truly publishes, such as pitch speed or a valid pitch-result-derived metric. Do not fabricate xwOBA if unavailable.

### #4 — Highest-usage non-FF pitch per pitcher vs same pitcher's FF

Acceptance points:

- exclude FF from candidate selection;
- apply minimum usage if configured;
- choose highest-usage eligible non-FF pitch independently for each pitcher;
- annotate selected pitch type back onto pitch-grain rows;
- compare candidate vs that same pitcher's FF;
- preserve tie semantics;
- manually verify several pitchers from raw usage counts.

### #5 — Nested arsenal grouping + within-group percentile + downstream comparison

Required chain:

```text
Arsenal Signature
-> Aggregate
-> Derived Field
-> Empirical Percentile within arsenal
-> cohort filter/group
-> downstream comparison
```

Acceptance points:

- generated `arsenal` can be consumed downstream;
- derived usage math is correct;
- percentile partition is truly within arsenal;
- current accepted mid-distribution tie semantics remain correct;
- resulting percentile/cohort field can drive another stage;
- no manual export/rejoin is required.

### #6 — Bounded follow event with between-condition flag

Original structure:

> After a target pitch, find the first subsequent occurrence of that pitch within the next N pitches and indicate whether FF occurred between them.

Acceptance points:

- target is the first qualifying later event;
- target occurs within configured next-N-pitches bound;
- partition does not cross plate appearances;
- between-condition flag is correct;
- inspect full raw pitch sequences for examples with between flag 0 and 1.

### #7 — Cross-grain pitcher-game vs pitcher-season comparison

Representative CPBL form:

> Per-game FF average velocity vs that pitcher's full-season FF average velocity.

Acceptance points:

- fine grain is pitcher + game;
- baseline grain is pitcher;
- baseline is constant across that pitcher's game rows;
- unit value varies by game;
- `difference = unit - baseline` exactly;
- no cross-pitcher joins;
- manually recompute at least one pitcher across multiple games.

### #8 — Arsenal set difference / change

Compare two real periods.

Acceptance points:

- Added and Removed pitch-type sets are correct;
- NULL pitch type is never treated as an arsenal member;
- only entities with pitch samples in both periods are compared;
- threshold semantics remain consistent between periods;
- spot-check actual raw pitch presence for selected Added/Removed examples.

### #9 — Per-pitcher empirical percentile threshold

Acceptance points:

- thresholds are computed per pitcher, not league-wide;
- empirical percentile/tie semantics match the accepted implementation;
- manually reconstruct the distribution for several pitchers and verify selected rows;
- large result paging/history/cache behavior should also be observed because the historical #9 workload exposed result-table/product performance issues.

Large-result UX checks:

- 200-row paging remains stable;
- page switching does not freeze;
- History reload returns the correct result;
- cache reload does not show blank/stale result;
- Save Analysis remains available;
- cache-status UI does not duplicate itself.

### #10 — Multi-stage selector + per-entity clustering + best cluster + FF comparison

Use real CPBL Trackman-native numerical features.

Recommended available feature candidates include:

- release speed;
- spin rate;
- HAA;
- VAA;
- release side / release height;
- other real continuous Trackman measurements shown valid by the Stage 7C census.

Acceptance points:

- minimum usage truly limits candidate pitch eligibility;
- candidate pitch is selected independently within the intended entity/arsenal scope;
- each pitcher is modeled independently;
- fixed-K behavior remains valid;
- Auto K remains valid if exercised;
- insufficient-complete-row entities are skipped and reported, not allowed to abort the whole run;
- best cluster is selected using an actual CPBL outcome/evaluation field;
- selected cluster is compared with that pitcher's FF reference;
- no identifier field is offered as a continuous numerical feature;
- circular variables, if used, retain the accepted periodic representation semantics.

Do not fabricate Savant-only `pfx_x`, `pfx_z`, or xwOBA fields. Preserve the research structure while substituting real CPBL measurements.

---

## 4. MLB regression after CPBL acceptance

Because CPBL shares the relational/numerical analysis implementation with MLB, Stage 7 must include an MLB regression pass after CPBL acceptance.

At minimum:

- run the existing automated MLB suite;
- run the original ten user-journey stress tests against MLB/Savant again, or an equivalently strong manual regression pass using the original fields;
- verify no CPBL dataset/profile work changed existing MLB semantics, field legality, cache/history behavior, or supplemental-data isolation.

This is regression confirmation, not a reopening of the already closed Stage 4 program.

---

## 5. Stage 7H — Final closure / release report

Produce `docs/CPBL_STAGE7_ACCEPTANCE_REPORT.md` containing at least:

### Dataset summary

- supported season window tested;
- source schedule games;
- completed games;
- mirrored games;
- games without Trackman;
- raw snapshot counts;
- identifiable pitch count;
- normalized pitch count;
- Trackman pitch count;
- pitches without Trackman measurements;
- duplicate pitch count;
- failed/unresolved source items.

### Coverage summary

- complete observed `AutoPitchType` values;
- complete observed `TaggedPitchType` values;
- complete observed `PitchCall` values;
- canonical mapping coverage;
- field-by-field non-NULL coverage;
- source-unavailable measurements;
- accepted source anomalies / exclusions.

### Lifecycle results

- clean backfill;
- resume;
- repeat/idempotency;
- update;
- retry;
- verify;
- raw rebuild;
- DuckDB full build;
- DuckDB incremental refresh.

### Analysis results

- SQLite/DuckDB parity summary;
- numerical-analysis real-data acceptance;
- original ten CPBL stress tests, each PASS/FAIL with evidence;
- MLB regression status.

### Performance results

Record machine/environment and cold/warm timings for the representative operations listed above.

### Remaining limitations

Every remaining item must be categorized as one of:

- upstream/source unavailable;
- accepted source data-quality limitation;
- non-blocking performance/presentation backlog;
- blocking defect.

Stage 7 cannot close with an unresolved blocking defect.

---

## 6. Final Stage 7 closure criteria

CPBL is considered fully complete only when all of the following are true:

1. All currently public completed games in scope have a reconciled acquisition state.
2. No unexplained game or identifiable-pitch loss remains.
3. Raw archives verify and can rebuild an analytically equivalent normalized database.
4. Repeated and overlapping synchronization is idempotent.
5. Full observed Trackman fields/enums/missingness are documented.
6. Native provider values remain preserved even when canonical mapping is unknown.
7. SQLite and DuckDB are analytically equivalent on real CPBL workloads.
8. All supported analysis mode families run successfully on full CPBL data.
9. The original ten CPBL real-user stress tests pass 10/10.
10. Large-result paging/cache/history/save/load and heavy numerical analysis have no blocking stability/UX defect.
11. MLB automated and real-user regression shows no CPBL-induced behavioral regression.
12. Missing measurements that CPBL does not publish are explicitly documented as `source unavailable`, not left as ambiguous TODOs.
13. `CPBL_STAGE7_ACCEPTANCE_REPORT.md` is committed with final counts, coverage, benchmarks, ten-test evidence, limitations, and acceptance commit SHA.

Only after all thirteen conditions are met should the CPBL analysis expansion be described as fully production-accepted / complete.
