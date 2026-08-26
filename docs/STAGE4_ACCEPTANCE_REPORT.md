# Stage 4 Formal Acceptance Report

Date: 2026-08-27  
Baseline: `main` at `cc0b3ff109610a5b03d7419ef1bf166d7956fe40`  
Scope: the ten original Stage 4 stress tests plus defects and UX findings discovered during real UI acceptance.

## Executive result before this remediation batch

| # | Stress test | Acceptance result | Evidence / blocking issue |
|---|---|---|---|
| 1 | Exact three Sweepers; all-consecutive vs none-adjacent cohorts; analyze third Sweeper | PARTIAL | Both sequence predicates and selected occurrence were correct in real Statcast spot checks, but Sequence Pattern was terminal and could not feed cohort comparison. |
| 2 | Arsenal signature, usage role, same-arsenal FF-rank cohort comparison | PARTIAL | Arsenal threshold/signature and role ranking passed; typed continuation was missing from the UI. |
| 3 | Three-game rising usage → fourth-game metric change | PASS | Conditional aggregate, derived usage, strict 3-value trend, Lead and fourth-game metric difference all executed correctly. |
| 4 | Highest-usage non-FF pitch per pitcher vs same pitcher's FF | PARTIAL | Dynamic non-FF selector passed; selected pitch could not be kept alongside FF for a downstream comparison. |
| 5 | Nested arsenal grouping + within-group percentile + downstream comparison | PARTIAL / product fail | Required pieces existed in separate modes, but arsenal signature and empirical percentile were not composable in Research Workflow. |
| 6 | Bounded follow event; first repeated Sweeper within 3 pitches; FF-between flag | PASS | Real PA spot check confirmed first matching target, bounded row gap and between-condition semantics. |
| 7 | Cross-grain pitcher-season vs pitcher-game comparison | PASS | Unit and baseline aggregates joined correctly and difference arithmetic matched. |
| 8 | Arsenal set difference / change | PARTIAL | Added/Removed sets executed, but NULL `pitch_type` could appear as an arsenal member. |
| 9 | Per-pitcher empirical percentile threshold | PASS core | Per-entity partition semantics were correct; 47,728-row result exposed severe result-table/cache UX problems. |
| 10 | Movement clustering / automatic clustering | PASS core | Per-entity K-means, standardization, summaries and assignments passed. Multi-stage Cluster Comparison separately failed because candidate selection ignored Minimum Usage. |

## Confirmed defects and UX findings

### Capability / correctness
- **CAP-01** — Sequence/EventPattern results were terminal; no two-cohort typed continuation.
- **CAP-02** — Arsenal Signature and Relative Pitch Selector existed in backend workflow support but were not exposed in the UI.
- **CAP-03** — Dynamic pitch selector could not annotate the original relation, so selected non-FF vs same-entity FF comparison was not expressible.
- **BUG-08A** — NULL `pitch_type` could enter pitch usage / arsenal set calculations.
- **BUG-10A** — Multi-stage Cluster Comparison used Minimum Usage for the displayed arsenal signature but not for candidate-pitch eligibility.

### Result handling / performance
- **PERF-UI-01** — Result rendering created the entire table DOM. 20,856 rows could freeze on rapid scroll; 47,728 rows could freeze immediately.
- **UX-10** — Row-limit configuration was inconsistent across analysis pages.
- **STATE/UX-01** — Results larger than the 8 MiB persistent-cache ceiling were recorded in History without a restorable result; Load could leave a blank or stale previous result without explanation.
- **UX-11** — Stage 4 cache state could be shown twice.

### Analysis-builder UX
- **UX-01** — Basic Analysis started with `Count + None`, making raw pitch-row inspection unintuitive.
- **UX-02** — Long field/checklist controls lacked search.
- **UX-03** — Advanced Research required raw field names without pipeline-aware autocomplete; generated aliases were not discoverable.
- **UX-04** — Analysis Progress could appear above Advanced Research settings because of dynamic insertion order.
- **UX-05** — Workflow stages could only be appended/removed; no reorder or insertion point.
- **UX-06** — Primary run-button wording differed by page.
- **UX-07** — Basic Group By needs Chrome-Find-like locate/highlight behavior rather than filtering the checklist.
- **UX-08** — Checkbox field lists did not show a persistent summary of selected fields.
- **UX-09** — Individual Percentile Threshold naming differed between sidebar, page heading and internal labels.
- **WORDING-01** — Follow-up Event “Maximum Row Gap” was easy to misread as number of pitches *between* events rather than target within the next N pitches.

## Remediation in this batch

This batch intentionally fixes the acceptance findings together rather than splitting them into separate product increments.

### Typed workflow composition
- Add **Event Pattern Cohorts** workflow stage. It can union selected event occurrences from multiple arrangements (for example `consecutive` and `none_adjacent`) and attach a cohort field for downstream aggregation.
- Expose existing **Arsenal Signature** and **Relative Pitch Selector** stages in Research Workflow.
- Add **Relative Pitch Annotation** stage. It preserves the original rows while attaching the dynamically selected pitch type per entity, enabling conditional metrics such as `pitch_type == selected_pitch_type` alongside `pitch_type == FF`.
- Add **Empirical Percentile** workflow stage with pipeline-aware partition fields.
- Conditional aggregate metrics accept another field as the comparison operand, not only a constant.
- Relative pitch stages can inherit Minimum Usage from a preceding Arsenal Signature stage; Multi-stage Cluster Comparison therefore cannot select an under-threshold rare pitch.

### Data semantics
- `pitch_usage()` excludes NULL pitch types before counts, usage rates, arsenal signatures, role selection and arsenal-change set operations.

### Result safety and history behavior
- Add a generic **Result Row Limit** to every analysis page; the server trims returned rows while preserving the full `row_count`.
- Add client-side 200-row paging so the DOM never needs to contain the full returned result at once.
- History/Saved Analysis loading always restores settings; when no stored result exists, the result pane is explicitly cleared and explains that re-execution is required.
- Deduplicate cache-status badges.

### Builder / navigation UX
- Remove the automatic Basic `Count + None` metric so no-metric raw-row mode is the natural default.
- Checkbox field lists gain search and a persistent selected-field summary. Basic Group By search locates/highlights without changing selection or hiding the list.
- Single field selectors gain a type-to-select field input backed by the legal field list; entering a valid raw field name is itself a selection.
- Advanced Research field inputs gain pipeline-aware datalist suggestions including aliases created by prior stages.
- Workflow stages gain Up, Down and “Add After” controls while preserving their configured DOM state.
- Normalize run buttons to **執行分析 Run Analysis**.
- Normalize **個別百分位門檻 Individual Percentile Threshold** naming.
- Move Analysis Progress directly before the result panel after all dynamically inserted settings panels.
- Rename Follow-up gap wording to **往後最多幾球內 Target Within Next N Pitches**.

## Deferred / not silently changed
- Whether Arsenal Change should exclude entities that have no pitches at all in one comparison period remains a semantic product decision. This acceptance run only confirmed the NULL-pitch-type bug; the batch does not silently redefine absence-in-period semantics.
- Full export/visualization/uncertainty presentation remains Stage 4D scope and is not part of this acceptance repair batch.
