# Stage 4A–4C Final Acceptance Report

Date: **2026-09-01**  
Branch: `refactor/unify-multifield-and-panel-lifecycle`  
Scope: Stage 4A performance/cache/workspace, Stage 4B composable relational workflow, Stage 4C Numerical Executor, all remediation discovered during real UI acceptance, CAP-04 Auto Cluster Count, RESEARCH-01 closure, and Supplemental Savant Data first-version integration.

## Executive result

**Stage 4A–4C: PASS / FORMALLY CLOSED.**

Closure criteria satisfied:

- ten original Stage 4 architecture stress tests have product paths and regression coverage;
- previously discovered correctness, performance, state and UX defects required for Stage 4A–4C closure are remediated;
- Stage 4 relational and numerical paths have persistent automated coverage;
- live Savant smoke tests pass;
- large-data benchmark and PERF-10A acceptance are complete;
- CAP-04 Auto K passes synthetic and natural-data acceptance;
- Pitch3D and Hawk-Eye spin/seam aggregate supplemental sources pass live fetch and manual data-management acceptance;
- supplemental fields are verified not to leak into the existing Statcast analyzer;
- RESEARCH-01 is closed at the current public-client boundary;
- Stage 4D Output / Visualization remains intentionally out of scope and is the next major product phase.

---

## 1. Original ten architecture stress tests — final status

| # | Stress test | Final status | Final capability |
|---|---|---|---|
| 1 | Exact three Sweepers; all-consecutive vs none-adjacent cohorts; analyze third Sweeper | PASS | EventPattern + Event Pattern Cohorts + typed downstream workflow |
| 2 | Arsenal signature, usage role, same-arsenal FF-rank cohort comparison | PASS | Arsenal Signature + Relative Pitch Selector + ties / minimum usage semantics |
| 3 | Three-game rising usage → fourth-game metric | PASS | Conditional aggregate + derived usage + consecutive-N + Lead |
| 4 | Highest-usage non-FF pitch per pitcher vs same pitcher's FF | PASS | Relative Pitch Selector + Relative Pitch Annotation |
| 5 | Nested arsenal grouping + within-group percentile + downstream comparison | PASS | Arsenal Signature + Empirical Percentile + composable workflow |
| 6 | Bounded follow event; first repeated Sweeper within N pitches; between-condition flag | PASS | FollowEvent with explicit next-N-pitches semantics |
| 7 | Cross-grain pitcher-season vs pitcher-game comparison | PASS | typed cross-grain aggregate / join |
| 8 | Arsenal set difference / change | PASS | Set Difference + Arsenal Change; NULL pitch type excluded |
| 9 | Per-pitcher empirical percentile threshold | PASS | Individual Threshold / empirical percentile partition semantics |
| 10 | Multi-stage selector + per-entity clustering + best cluster + FF comparison | PASS | Multi-stage Cluster Comparison + per-entity clustering; CAP-04 adds Auto K |

The ten questions remain permanent regression / acceptance requirements. Passing Stage 4A–4C does not remove them.

---

## 2. Acceptance remediation completed before closure

### 2.1 Typed workflow composition

Completed:

- Event Pattern Cohorts can union selected event occurrences from multiple arrangements and attach cohort fields for downstream analysis.
- Arsenal Signature and Relative Pitch Selector are exposed in Research Workflow.
- Relative Pitch Annotation preserves original rows while attaching the dynamically selected pitch type per entity.
- Empirical Percentile is composable in the workflow.
- Conditional aggregate metrics can compare against another field, not only a constant.
- Relative pitch stages can inherit Minimum Usage from Arsenal Signature.

### 2.2 Data semantics

Completed:

- `pitch_usage()` excludes NULL pitch types before counts, usage rates, arsenal signatures, role selection and arsenal-change set operations.
- Arsenal Change only compares entities with pitch samples in both requested periods.
- ties and low-sample policies are explicit rather than silently selecting arbitrary rows.

### 2.3 Result safety, cache and history

Completed:

- generic Result Row Limit across analysis pages;
- 200-row client paging to prevent giant DOM tables;
- full internal numerical assignments remain available even when UI display is limited;
- History / Saved Analysis restore settings even when a result was too large to persist;
- stale/blank result behavior is explicitly cleared and explained;
- duplicate cache-status badges removed;
- cache validity remains tied to canonical payload + data revision + backend + cache format.

### 2.4 Analysis-builder UX

Completed:

- Basic raw-row mode no longer starts with invalid `Count + None` assumptions;
- checkbox field lists have search / selected summaries;
- single-field selectors support direct valid-field entry;
- Research Workflow field suggestions include prior aliases;
- workflow stages support reorder / insertion;
- run wording normalized to `執行分析 Run Analysis`;
- Individual Percentile Threshold naming normalized;
- Analysis Progress placement normalized;
- Follow-up wording changed to `往後最多幾球內 Target Within Next N Pitches`;
- later field-control and panel-lifecycle remediation keeps dynamic controls on the shared contract.

---

## 3. Named correctness / performance / UX acceptance retained at closure

The following items had already been independently accepted and remain part of the closure baseline:

- `BUG-10A` — PASS.
- `BUG-10B` — PASS.
- `RS-01` — PASS. Empirical percentile uses `P(X<x) + 0.5 P(X=x)`; all tied values produce 0.5.
- `RS-02` — PASS. Circular angle features such as `spin_axis` are represented by sin/cos pairs and standardized without destroying circular geometry.
- `FIELD-01` — PASS. Identifier fields remain semantically excluded from numerical feature capability and UI bypass paths are blocked.
- `PERF-10A` — PASS. Representative #10 workload improved from roughly 60 s to 28.1 s while preserving 3,918 output rows and 801 best-cluster matches.
- `WORDING-01` — PASS.
- `UX-12` through `UX-15` — PASS.
- original #9 non-core result UX acceptance remains PASS and is not reopened.

---

## 4. Stage 4A final acceptance

Stage 4A includes performance, cache and analysis-workspace behavior.

Accepted capabilities:

- persistent result cache;
- Analysis History;
- Save / Load / Delete Analysis;
- explicit separation of `analysis_state.sqlite3` from Statcast source-of-truth data;
- DuckDB analytical mirror path;
- data-revision-aware cache invalidation;
- large-result paging / display safety;
- persistent fast status;
- full-data benchmark.

Full-data benchmark baseline retained:

- Statcast rows: 9,192,548.
- warmed SQLite representative query median: about 2.31–2.35 s.
- warmed DuckDB representative query median: about 0.07–0.08 s.
- first DuckDB mirror construction: about 245.5 s, reported separately from query time.

Conclusion: ordinary large relational analysis uses DuckDB primary path; SQLite remains source-of-truth/fallback/correctness path. Minute-scale ordinary relational queries are profiling targets, not accepted as inevitable.

**Stage 4A final status: PASS.**

---

## 5. Stage 4B final acceptance

Stage 4B provides the composable relational workflow.

Accepted stage families include:

- Aggregate / conditional aggregate;
- Derived arithmetic;
- Filter;
- Rolling Window;
- Lag / Lead;
- Consecutive-N trend;
- First / Last / Nth;
- Within-group Rank;
- Project / Sort;
- Arsenal Signature;
- Relative Pitch Selector;
- Relative Pitch Annotation;
- Event Pattern Cohorts;
- Empirical Percentile.

DuckDB / SQLite ratio semantics parity is covered. Workflow stages preserve typed/grain semantics and generated aliases can feed later stages.

**Stage 4B final status: PASS.**

---

## 6. Stage 4C final acceptance

### 6.1 Numerical contract

Accepted:

- `NumericalTable` with explicit columns / rows / grain;
- `NumericalSection` typed outputs;
- grain-preserving clustering continuation;
- deterministic seeds;
- explicit Max Input Rows guard;
- no silent numerical truncation or sampling;
- UI row limits do not destroy full internal assignment data.

### 6.2 Clustering

Accepted:

- K-means;
- Gaussian Mixture;
- optional feature standardization;
- global or `Partition By` per-entity models;
- cluster sample size / center / mean / SD summary;
- GMM assignment probability;
- Multi-stage Cluster Comparison.

### 6.3 CAP-04 — Auto Cluster Count

**Final status: PASS.**

Contract:

- `K=1` must be a valid candidate.
- adaptive maximum K is derived from sample size.
- minimum cluster size protects against tiny artificial clusters.
- every candidate returns diagnostics: candidate K, criterion, score, valid, selected, cluster sizes, minimum cluster size, adaptive max K, rejection reason.
- per-partition Auto K can choose different K for different entities.
- manual K behavior remains unchanged.

Selection rules:

- Gaussian Mixture: BIC.
- K-means: full-covariance Gaussian Mixture BIC is used only as the K selector; the selected K is then fitted by real K-means.

The initial simplified K-means criterion was rejected during development because a clear two-group synthetic case over-split to K=8. The final implementation was not accepted until the selector passed known-answer cases.

Synthetic acceptance:

- one Gaussian sample → K=1;
- two clearly separated groups → K=2;
- tiny candidate clusters rejected;
- partition A one group / partition B two groups → K=1 / K=2 respectively;
- manual K path unchanged.

### 6.4 Natural CAP-04 acceptance — Max Scherzer 2024 FC + SL

Purpose: test a real pitcher whose Statcast pitch labels split two offerings that are not clearly separated in the chosen continuous feature space.

Configuration:

```text
Pitcher = 453286
Season = 2024
Pitch Type IN FC,SL
Partition By = empty
Features = release_speed,pfx_x,pfx_z,release_spin_rate
ID Fields = pitch_uid,pitcher
Standardize = true
Seed = 42
Auto K = true
```

The Statcast `pitch_type` label is only a filter and is deliberately **not** a clustering feature.

Observed complete-feature sample: **189 pitches**.

K-means Auto K:

- selected K = **1**;
- adaptive max K = 8;
- minimum cluster size = 6;
- K=1 selector BIC = 2152.270585;
- K=2 selector BIC = 2206.783363;
- scores continue worsening for higher K.

Gaussian Mixture Auto K:

- selected K = **1**;
- K=1 BIC = 2152.270585;
- K=2 BIC = 2206.783363;
- higher K values remain worse.

Interpretation: this acceptance does not redefine Scherzer's pitch taxonomy. It verifies that the unsupervised model is allowed to conclude that adding a second statistical component is not justified by these four standardized features, despite the source labels containing FC and SL.

### 6.5 Regression

Accepted:

- Linear OLS coefficients, SE, t statistic, p value, CI, R², RMSE, df;
- Binary Logistic coefficients, accuracy, log loss;
- optional predictor standardization;
- synthetic `y = 2 + 3x` known-answer recovery.

Logistic inferential coefficient SE / p / CI remain intentionally unavailable in the first version and are represented as NULL rather than fabricated.

### 6.6 Bootstrap

Accepted:

- mean / median / proportion;
- optional A-B difference;
- explicit resampling unit required;
- percentile confidence interval;
- deterministic seed;
- stratified unit resampling where group structure requires it;
- sufficient-summary fast paths for mean/proportion;
- explicit refusal of unsupported oversized row-wise workloads.

**Stage 4C final status: PASS.**

---

## 7. RESEARCH-01 closure

Formal research record: `docs/RESEARCH_01_HAWKEYE_SEAM_ORIENTATION.md`.

Final boundary:

- Hawk-Eye / MLB upstream has higher-dimensional spin/seam information.
- Savant player pages publicly expose player × season × pitch_type aggregate `serverVals.spinAxis` data including `image_spin_x/y/z`, `image_orientation_angle`, measured/inferred spin direction, active spin and related fields.
- standard Statcast exposes per-pitch 2D `spin_axis`.
- Pitch3D exposes continuous trajectory polynomial data.
- no stable public per-pitch seam-orientation / absolute ball-pose / quaternion / rotation-matrix / seam-phase / full orientation time-series endpoint was found across the inspected public Baseball Savant / MLB browser surfaces.

Policy after research:

- do not fabricate per-pitch pose from aggregate data;
- do not relabel `spin_axis` or `360-spin_axis` as per-pitch Hawk-Eye measured seam pose;
- park true per-pitch seam-pose integration until a legitimate verifiable source exists.

**RESEARCH-01 final status: CLOSED at current public-client boundary.**

---

## 8. Supplemental Savant Data first-version acceptance

These sources are intentionally data-management-only in the current product. They do not extend the existing Statcast analyzer yet.

### 8.1 Pitch3D MLB / MiLB

Storage / lifecycle requirements accepted:

- complete source CSV fields preserved with dynamic schema;
- MLB and MiLB namespaces separated;
- source-level pitch identity based on `game_pk + play_id` where available;
- raw gzip snapshots with hash / fetch metadata;
- Backfill;
- Resume;
- Update;
- Retry Failed path;
- Verify;
- Rebuild;
- independent progress state / UI.

Manual Ohtani `660271` acceptance:

- MLB Backfill: 10,118 rows, failed 0.
- MiLB Backfill: 135 rows, failed 0.
- second Backfill with Resume: skipped 1 for each previously successful unit.
- Update: MLB 10,118 rows; MiLB 135 rows; failed 0.
- Verify after repeated fetches: duplicate row keys 0, missing snapshot files 0, hash mismatches 0, `ok=true`.
- Rebuild restored expected row counts from raw snapshots.

### 8.2 Hawk-Eye spin / seam orientation aggregate

Storage grain:

```text
player × season × pitch_type
```

Representative preserved source fields:

```text
image_spin_x
image_spin_y
image_spin_z
image_orientation_angle
hawkeye_measured
movement_inferred
active_spin
alan_active_spin_pct
spin_rate
n_pitches
```

Manual Ohtani `660271` acceptance:

- Backfill: 34 rows, failed 0.
- Resume: skipped 1 after prior success.
- Update: 34 rows, failed 0.
- Verify: duplicate row keys 0, missing snapshot files 0, hash mismatches 0, `ok=true`.
- Rebuild restored 34 rows from raw snapshot.
- dataset metadata confirmed as `mlb`.

### 8.3 Existing analyzer isolation

Explicit product requirement: future analysis should eventually support multiple data sources, but the already-built Statcast analyzer must not silently absorb the new sources now.

Manual acceptance performed after supplemental data existed locally:

- Pitch3D polynomial / trajectory fields were not visible in existing Basic Analysis field lists.
- Hawk-Eye `image_spin_x`, `image_orientation_angle` and related fields were not visible.
- attempting direct field entry did not resolve them as legal existing-Statcast analysis fields.

Therefore the current analyzer contract remains isolated while supplemental data stays available for a future grain-aware multi-source analysis architecture.

**Supplemental data first-version status: PASS.**

Full historical download is not a closure requirement because there is not yet a production multi-source analysis consumer; small live samples are sufficient for functional acceptance.

---

## 9. Automated / live validation at closure

The implementation integration batch completed:

- persistent test suite: **184 passed, 2 deselected**;
- live Savant integration: **2 passed, 184 deselected**;
- live supplemental source probes: PASS;
- synthetic CAP-04 acceptance: PASS;
- Scherzer 2024 FC+SL natural acceptance: PASS.

The implementation branch was subsequently run through the normal CI path with both `test` and `live-savant-smoke` jobs successful before this documentation closure.

Documentation-only closure commits must also leave CI green; the final branch status is checked after these report updates.

---

## 10. Deferred scope after Stage 4A–4C closure

### Stage 4D — Output / Visualization

Intentionally not included in Stage 4A–4C:

- formal charts / visualization;
- export;
- sample-size / uncertainty visualization;
- richer comparison result presentation;
- deeper preset / library UX;
- optional future AI → AST.

Charts must consume the formal analysis result contract. They must not reimplement a second statistics engine in the frontend.

### Future multi-source analysis

Also not part of Stage 4A–4C:

- Statcast ↔ Pitch3D pitch-level analysis joins;
- legal aggregation/join semantics for player × season × pitch_type spin aggregates;
- source provenance and field conflict UX;
- DuckDB analytical integration of supplemental tables;
- multi-source cache / data-revision semantics.

### True per-pitch seam pose

Not implementable from the public sources found by RESEARCH-01. Remains parked until a legitimate, verifiable source exists.

---

## Final closure statement

As of 2026-09-01:

**Stage 4A — PASS / CLOSED**  
**Stage 4B — PASS / CLOSED**  
**Stage 4C — PASS / CLOSED**  
**CAP-04 — PASS / CLOSED**  
**RESEARCH-01 — CLOSED at current public-client boundary**  
**Supplemental Pitch3D / Hawk-Eye aggregate first version — PASS**  
**Existing Statcast analyzer isolation — PASS**  
**Stage 4D — NOT STARTED; next major product phase**
