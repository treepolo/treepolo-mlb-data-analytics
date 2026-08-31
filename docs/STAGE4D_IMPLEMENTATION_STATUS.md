# Stage 4D — Implementation Status

Date: **2026-09-01**  
Branch: `refactor/unify-multifield-and-panel-lifecycle`

## Current status

**Stage 4D first implementation is code-complete for the agreed first-version scope and has passed automated acceptance. Manual browser/UI acceptance is still pending, so Stage 4D is not yet formally closed.**

Formal product/architecture contract: `docs/STAGE4D_SPEC.md`.

## Implemented

- Independent left-nav `Output` group with:
  - `Visualization`
  - `Analysis Library`
  - `Analysis History`
- Analysis Result toolbar actions:
  - `匯出 Export`
  - `送至視覺化 Open in Visualization`
- Independent single-chart Visualization workspace. The first version is single-chart, while `VisualizationSpec` and storage are intentionally composable by a future multi-chart/dashboard entity.
- Visualization sources:
  - current analysis result
  - recent session results
  - Analysis History
  - Saved Analyses
  - Saved Visualizations
- Multi-section result selection.
- Presentation metadata contract with field type, role, unit, identifier/temporal/category/numeric semantics.
- Generic presentations:
  - line
  - bar
  - scatter
  - point/range
  - dumbbell
  - difference
- Built-in baseball/statistical presets including:
  - Pitch Movement
  - Pitch Location + strike-zone/plate overlay
  - Release Point
  - Pitch Usage Trend
  - Cluster Map
  - Auto-K Diagnostics
  - Regression Coefficients
  - Confidence Interval
  - Cross-Level Comparison
  - Difference Ranking
- Sample-size display and explicit provenance.
- Large-data handling:
  - Full Data
  - Automatic Sampling
  - Manual Sampling
  - Random / Every Nth Row
  - deterministic seed
  - visible sampled-row disclosure
  - no silent truncation
- Saved Visualization:
  - Live
  - Frozen
- Frozen visualization snapshots stored as gzip JSON outside the Statcast source-of-truth database.
- User visualization presets.
- Data export:
  - CSV
  - JSON
  - XLSX
  - Parquet
- Figure export:
  - SVG
  - PNG
  - Copy Image when browser Clipboard image API is available
- Report output:
  - HTML
  - PDF
- Full-result export / report preparation reuses the same Analysis Payload and explicitly removes only the UI-only `result_limit`; it does not serialize the paged DOM table or pretend the front-end retained rows are the complete result.
- Stage 4D presentation tables are stored in `analysis_state.sqlite3`; Statcast source-of-truth remains separate.
- Baseball graphical asset policy is constrained to `research_assets/3d_baseball/` and its pinned fetch helper/manifest. Stage 4D does not search for or introduce a replacement baseball/seam asset.

## Automated acceptance

Latest persistent suite after Stage 4D implementation:

```text
197 passed, 2 deselected
```

The Stage 4D acceptance tests cover:

- field metadata roles/units;
- deterministic automatic/manual sampling;
- unsafe full-visualization row refusal;
- full-result rerun with UI `result_limit` removed;
- Live/Frozen persistence and frozen snapshot deletion;
- CSV/JSON/XLSX/Parquet generation;
- HTML/PDF report generation;
- HTML SVG sanitization;
- built-in preset contract;
- single-chart-now / future-multi-chart compatibility rule;
- project-only baseball-asset policy;
- UI/nav/export/report wiring.

CI additionally runs:

```text
node --check src/treepolo_mlb_data/web_static/stage4d-visualization.js
```

Latest CI on the implementation head passed both the persistent test job and live Savant smoke job.

## Manual acceptance still required

Before Stage 4D formal closure, run browser acceptance against real local analysis results. At minimum validate:

1. Output navigation and separated Visualization / Analysis Library / Analysis History pages.
2. `Open in Visualization` from a normal relational result and a multi-section numerical result.
3. Built-in Pitch Movement, Pitch Location, Auto-K, Regression coefficient, and Bootstrap/CI presentations when source columns exist.
4. Full / Automatic / Manual sampling disclosure.
5. Live save → reload and Frozen save → reload.
6. User preset save/reuse.
7. CSV, JSON, XLSX, Parquet data exports open correctly and are not front-end-row truncated.
8. SVG and PNG figure export.
9. HTML and PDF report output.
10. Analysis History / Saved Analysis result-unavailable path requires explicit re-run and does not silently execute.

Any defect discovered by this manual acceptance belongs to Stage 4D remediation before formal closure.
