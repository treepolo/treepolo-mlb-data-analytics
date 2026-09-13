# Stage 4 Final Acceptance Report

Updated: **2026-09-13**  
Branch: `refactor/unify-multifield-and-panel-lifecycle`

This document is the consolidated acceptance record for Stage 4. Stage 4A–4C were first formally closed on 2026-09-01; Stage 4D was implemented and manually accepted afterward. The former wording that described Stage 4D as “not started” or “the next major product phase” is historical and no longer represents the project state.

## Executive result

**Stage 4A — PASS / CLOSED**  
**Stage 4B — PASS / CLOSED**  
**Stage 4C — PASS / CLOSED**  
**Stage 4D — PASS / CLOSED**  
**CAP-04 Auto Cluster Count — PASS / CLOSED**  
**RESEARCH-01 — CLOSED at current public-client boundary**  
**Supplemental Pitch3D / Hawk-Eye aggregate first version — PASS**  
**Existing Statcast analyzer isolation — PASS**

There is no currently defined Stage 4E or other numbered next phase.

Formal Stage 4D product/engineering contract: `docs/STAGE4D_SPEC.md`.  
Detailed Stage 4D final implementation/manual status: `docs/STAGE4D_IMPLEMENTATION_STATUS.md`.

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

These ten questions remain permanent regression / acceptance requirements.

---

## 2. Stage 4A — performance, cache and analysis workspace

Accepted capabilities:

- persistent result cache;
- canonical payload + data revision + backend + cache-format key;
- Analysis History;
- Saved Analysis persistence;
- cached-result restoration when available;
- explicit separation of `analysis_state.sqlite3` from Statcast source-of-truth data;
- DuckDB analytical mirror path with SQLite fallback;
- result-row safety / paging / large-result history behavior;
- persistent fast status;
- full-data benchmark.

Full-data benchmark baseline retained:

- Statcast rows: **9,192,548**;
- warmed SQLite representative query median: about **2.31–2.35 s**;
- warmed DuckDB representative query median: about **0.07–0.08 s**;
- first DuckDB mirror construction: about **245.5 s**, reported separately from query time.

**Stage 4A: PASS / CLOSED.**

---

## 3. Stage 4B — composable relational workflow

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

Important acceptance remediation retained:

- Event Pattern Cohorts can combine selected occurrences and attach cohort fields for downstream analysis;
- Relative Pitch Selector/Annotation and Arsenal Signature compose inside Research Workflow;
- conditional metrics can compare against another field;
- `pitch_usage()` excludes NULL pitch types before usage/arsenal/set calculations;
- Arsenal Change only compares entities with pitch samples in both periods;
- ties and low-sample behavior are explicit.

**Stage 4B: PASS / CLOSED.**

---

## 4. Stage 4C — Numerical Executor

### Numerical contract

Accepted:

- `NumericalTable` with explicit columns / rows / grain;
- `NumericalSection` typed outputs;
- grain-preserving clustering continuation;
- deterministic seeds;
- explicit Max Input Rows guard;
- no silent numerical truncation or sampling;
- UI row limits do not destroy full internal assignment data.

### Clustering

Accepted:

- K-means;
- Gaussian Mixture;
- optional feature standardization;
- global or `Partition By` per-entity models;
- cluster sample size / center / mean / SD summary;
- GMM assignment probability;
- Multi-stage Cluster Comparison.

### CAP-04 — Auto Cluster Count

**PASS / CLOSED.**

Contract:

- `K=1` is a valid candidate;
- adaptive maximum K derives from sample size;
- minimum cluster size rejects tiny artificial clusters;
- every candidate exposes criterion/score/valid/selected/cluster-size diagnostics;
- per-partition Auto K may choose different K by entity;
- manual K behavior remains unchanged.

Selection rules:

- Gaussian Mixture: BIC;
- K-means: full-covariance Gaussian Mixture BIC is used only as the K selector, then real K-means fits the selected K.

Known-answer acceptance includes one-Gaussian → K=1, clear-two-group → K=2, tiny-cluster rejection and partition-specific K. Natural acceptance with Max Scherzer 2024 FC+SL used 189 complete-feature pitches; K-means Auto K and Gaussian Mixture Auto K both selected **K=1**.

### Regression / Bootstrap

Accepted:

- Linear OLS coefficients, SE, t statistic, p value, CI, R², RMSE, df;
- Binary Logistic coefficients, accuracy, log loss;
- predictor standardization;
- synthetic `y = 2 + 3x` known-answer recovery;
- Bootstrap mean / median / proportion;
- optional A-B difference;
- explicit resampling unit;
- percentile confidence interval;
- deterministic seed;
- stratified unit resampling where required;
- explicit refusal of unsupported oversized row-wise workloads.

Logistic inferential coefficient SE / p / CI remain unavailable in the first version and are represented as NULL rather than fabricated.

**Stage 4C: PASS / CLOSED.**

---

## 5. Stage 4D — Output / Visualization

Stage 4D was intentionally outside the original 4A–4C closure scope, but it was subsequently implemented and accepted. It must no longer be described as pending or “next”.

Accepted product surface:

- Output navigation group: Visualization / Analysis Library / Analysis History;
- table-first result pages with Export and Open in Visualization;
- independent single-chart Visualization workspace;
- current/recent/History/Saved Analysis/Saved Visualization sources;
- multi-section result selection;
- presentation metadata and provenance;
- line / bar / scatter / range / dumbbell / difference presentation;
- built-in baseball/statistical presets;
- Full / Automatic / Manual sampling with explicit disclosure;
- Saved Visualization Live / Frozen v2;
- User Presets;
- CSV / JSON / XLSX / Parquet full-result export;
- SVG / PNG / Copy Image figure export;
- bilingual HTML / PDF report output;
- Analysis History persistent IDs;
- Analysis Library saved-analysis Name/Notes editing through one `編輯 Edit` action.

The original Stage 4D manual acceptance sequence was **1–15**, and all fifteen items passed. Important real-data checks included:

- manual random sampling 50 rows with deterministic seed;
- a source with **18,887 rows** exported completely to CSV, JSON, XLSX and Parquet even though Visualization was sampled to 50 rows;
- Parquet validated as a real readable 18,887-row file;
- standalone SVG/PNG styling matched the on-screen chart after export-fidelity remediation;
- HTML and PDF reports passed manual inspection for bilingual labels, chart fidelity and non-clipping wide tables;
- Analysis Library / Analysis History navigation and loading remained functional;
- post-acceptance Analysis Library `編輯 Edit` prefilled Name/Notes and updated the same saved item without creating a duplicate.

Acceptance-cycle remediation also covered:

- latest-request-wins when rapidly switching Visualization sources;
- cross-source Result Section reset;
- Saved Visualization restore generation guards;
- Frozen v2 content-addressed multi-section snapshots;
- standalone export style embedding;
- Parquet bulk export and visible export progress;
- bilingual responsive report rendering.

Some preset/renderer branches were data-dependent during manual testing (for example Pitch Location/Release Point when required fields were unavailable, or stacked bars when only one series existed). Those were explicitly accepted as non-blocking coverage gaps and do not reopen Stage 4D.

**Stage 4D: PASS / CLOSED.**

---

## 6. RESEARCH-01 closure

Formal research record: `docs/RESEARCH_01_HAWKEYE_SEAM_ORIENTATION.md`.

Final boundary:

- Hawk-Eye / MLB upstream has higher-dimensional spin/seam information;
- Savant publicly exposes player × season × pitch_type aggregate `serverVals.spinAxis` data including 3D spin/orientation fields;
- standard Statcast exposes per-pitch 2D `spin_axis`;
- Pitch3D exposes continuous trajectory polynomial data;
- no stable public per-pitch seam-orientation / absolute ball-pose / quaternion / rotation-matrix / seam-phase / full orientation time-series endpoint was found across inspected public Baseball Savant / MLB browser surfaces.

Policy: do not fabricate per-pitch pose from aggregate data and do not relabel `spin_axis` as measured seam pose.

**RESEARCH-01: CLOSED at current public-client boundary.**

---

## 7. Supplemental Savant Data first-version acceptance

These sources remain data-management-only in the current product and do not silently extend the existing Statcast analyzer.

### Pitch3D MLB / MiLB

Accepted lifecycle: complete source fields, dataset namespaces, raw gzip snapshots, Backfill, Resume, Update, Retry Failed, Verify, Rebuild and independent progress.

Manual Ohtani `660271` acceptance:

- MLB: 10,118 rows;
- MiLB: 135 rows;
- repeated Resume/Update/Verify/Rebuild: PASS;
- duplicate row keys 0, missing snapshot files 0, hash mismatches 0.

### Hawk-Eye spin/seam aggregate

Storage grain: `player × season × pitch_type`.

Manual Ohtani `660271` acceptance:

- 34 rows;
- Resume/Update/Verify/Rebuild: PASS;
- dataset metadata = `mlb`.

### Existing analyzer isolation

Pitch3D polynomial/trajectory fields and spin aggregate fields were verified absent from existing Statcast analysis field controls and unavailable through direct field-entry bypasses.

**Supplemental first-version status: PASS.**

---

## 8. Automated / live validation

The original 4A–4C closure recorded a persistent suite of 184 passed / 2 deselected plus live Savant integration success. Development continued through Stage 4D remediation.

The latest accepted implementation head before this documentation refresh reported:

```text
226 passed, 2 deselected
live-savant-smoke: PASS
```

CI additionally checks the active Stage 4D JavaScript modules, the latest-request race regression, realistic Parquet round-trip, report/export regressions and saved-analysis edit wiring.

---

## 9. Remaining backlog is not a numbered next stage

Potential future work remains possible, but none of it currently constitutes Stage 4E:

- explicit grain-aware Statcast ↔ Pitch3D / spin-aggregate multi-source analysis;
- long-term data/storage/scheduler operational validation;
- further real-world numerical known-answer cases;
- optional presentation UX improvements;
- possible multi-chart/dashboard/report composition if a future product requirement justifies it;
- true per-pitch seam pose only if a legitimate verifiable source becomes available.

AI → AST was explicitly removed from Stage 4D and is not implied by Stage 4 closure.

---

## Final closure statement

As of 2026-09-13:

**Stage 4A — PASS / CLOSED**  
**Stage 4B — PASS / CLOSED**  
**Stage 4C — PASS / CLOSED**  
**Stage 4D — PASS / CLOSED**  
**CAP-04 — PASS / CLOSED**  
**RESEARCH-01 — CLOSED at current public-client boundary**  
**Supplemental Pitch3D / Hawk-Eye aggregate first version — PASS**  
**Existing Statcast analyzer isolation — PASS**

The Stage 4 acceptance program is complete. Any later feature work should be recorded as maintenance/backlog unless a new phase is explicitly defined.
