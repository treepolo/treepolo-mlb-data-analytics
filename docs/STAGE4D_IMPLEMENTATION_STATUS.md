# Stage 4D — Implementation Status

Date: **2026-09-13**  
Branch: `refactor/unify-multifield-and-panel-lifecycle`

## Current status

**Stage 4D: PASS / FORMALLY CLOSED.**

The agreed first-version scope is implemented, automated validation is green, and the original browser/manual acceptance set **1–15** has been completed successfully. There is no currently planned Stage 4E or other numbered follow-on phase.

Formal product/architecture contract: `docs/STAGE4D_SPEC.md`.

## Implemented

- Left navigation keeps `Data` first, analysis pages in the middle, and an `Output` group at the bottom containing:
  - `Visualization`
  - `Analysis Library`
  - `Analysis History`
- Analysis Result actions include `匯出 Export` and `送至視覺化 Open in Visualization` while the result pages remain table-first.
- Independent single-chart Visualization workspace with a serializable `VisualizationSpec`; the data model remains compatible with a possible future multi-chart/dashboard layer without implying a planned next phase.
- Visualization sources:
  - current analysis result
  - recent session results
  - Analysis History
  - Saved Analyses
  - Saved Visualizations
- Multi-section result selection and source/section lifecycle guards.
- Presentation metadata for field labels/types/roles/units and identifier/temporal/category/numeric semantics.
- First-version presentation types:
  - line
  - bar, including vertical/horizontal and grouped/stacked behavior when the source supports it
  - scatter
  - point/range
  - dumbbell
  - difference
- Built-in presets currently shipped:
  - Pitch Movement
  - Pitch Location
  - Release Point
  - Pitch Usage Trend
  - Cluster Map
  - Auto-K Diagnostics
  - Regression Coefficients
  - Confidence Interval
  - Cross-Level Comparison
  - Difference Ranking
  - Generic Time Trend
  - Category Comparison
- Large-data handling:
  - Full Data
  - Automatic Sampling
  - Manual Sampling
  - Random / Every Nth Row
  - deterministic seed
  - visible sampled/total-row disclosure
  - no silent truncation
- Saved Visualization:
  - Live
  - Frozen
- Frozen v2 snapshots are content-addressed SHA-256 gzip JSON files, can retain the full multi-section result, deduplicate identical payloads, and are reference-aware when cleaned up. Snapshot metadata is stored separately from the Statcast source-of-truth database.
- User visualization presets.
- Data export:
  - CSV
  - JSON
  - XLSX
  - Parquet
- Figure export:
  - SVG
  - PNG
  - Copy Image when the browser Clipboard image API is available
- Report output:
  - HTML
  - PDF
  - bilingual fixed labels
  - responsive HTML result tables
  - landscape/wrapped wide PDF result tables when required
  - chart grid/axis/legend/reference-line fidelity
- Full-result export/report preparation resolves or reruns the formal analysis source when required and does not serialize the paged DOM table or treat retained/sampled browser rows as the complete export population.
- Stage 4D presentation tables remain in `analysis_state.sqlite3`; Statcast source-of-truth remains separate.
- Baseball graphical asset policy remains constrained to `research_assets/3d_baseball/` and its pinned manifest/fetch helper.
- Analysis History shows the persistent history ID used by Visualization source selection.
- Analysis Library supports Save / Load / Delete plus one `編輯 Edit` action for editing the saved analysis **Name** and **Notes** in the shared XP-style dialog without creating a duplicate.

## Reliability remediation accepted during manual testing

The acceptance cycle also closed defects that were not visible in the initial automated implementation pass:

- Visualization data requests use **latest request wins** so rapid source switching cannot let stale responses become UI-final.
- Changing source resets a stale Result Section before the next load, preventing a section index from a multi-section result leaking into a source with fewer sections.
- Saved Visualization restore is generation-guarded and restores the saved presentation only after source data/preset initialization is stable.
- Standalone SVG and PNG exports carry the presentation styles required for gridlines, axes, labels, legend and reference lines.
- Parquet export uses DuckDB bulk ingest instead of row-by-row `executemany`, and the UI exposes an explicit `匯出中 Exporting…` state.
- Report HTML/PDF fixed labels are bilingual and wide tables no longer clip or overflow the page.

## Automated acceptance

Latest accepted branch CI before this documentation refresh:

```text
226 passed, 2 deselected
live Savant smoke: PASS
```

CI also performs Node syntax checks for the active Stage 4D browser modules, runs the `stage4d-latest-request` race regression test, and covers Stage 4D presentation, persistence, export and report behavior.

Notable automated regressions include:

- deterministic automatic/manual sampling;
- unsafe full-visualization row refusal;
- full-result resolution with UI-only `result_limit` removed;
- Frozen v2 content-addressed multi-section snapshots;
- latest-request-wins and source/section reset behavior;
- CSV/JSON/XLSX/Parquet generation;
- realistic **18,887-row Parquet round-trip** through DuckDB;
- self-contained SVG export styling;
- bilingual responsive HTML/PDF reports, including an actual wide 10-column Auto-K PDF case;
- Analysis History ID rendering;
- Analysis Library Name/Notes edit wiring;
- project-only baseball-asset policy.

## Manual acceptance — final result

The original **1–15** acceptance sequence is complete:

1. Output navigation — PASS.
2. Clustering source/run path — PASS.
3. Open in Visualization — PASS.
4. Multi-section switching — PASS.
5. Auto-K Diagnostics — PASS.
6. Generic Scatter — PASS.
7. Pitch Movement — PASS; Pitch Location/Release Point were source-field-dependent and their unavailable-data cases were non-blocking.
8. Bar vertical/horizontal — PASS; stacked behavior remained source-series-dependent and non-blocking for the tested dataset.
9. Full / Automatic / Manual sampling, deterministic seed — PASS.
10. Saved Visualization Live/Frozen, reload and persistence — PASS.
11. User Preset — PASS.
12. CSV / JSON / XLSX / Parquet data export — PASS. A real 18,887-row result was exported completely despite a 50-row Visualization sample.
13. SVG / PNG figure export — PASS.
14. HTML / PDF report — PASS.
15. Analysis Library / Analysis History — PASS.

Post-acceptance product polish, also manually verified:

- Analysis Library `編輯 Edit` correctly pre-fills Name and Notes, updates the same Saved Analysis, and does not create a duplicate.

## Closure

Stage 4D is no longer pending manual acceptance and must not be described elsewhere as “not started”, “next phase”, or “awaiting closure”. Future maintenance or feature ideas belong to the general backlog unless a new project phase is explicitly defined later.
