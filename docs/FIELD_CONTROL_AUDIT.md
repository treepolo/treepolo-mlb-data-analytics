# Field Control Audit

Date: 2026-08-27

This audit follows the UI acceptance finding that searchable field controls must preserve the application's classic Windows XP-style interaction instead of replacing native selects with browser-native `datalist` text boxes.

## Interaction rules

1. **Multiple-field selectors** keep the existing checkbox-list UI, search box, and selected-item summary. They are not converted to text inputs.
2. **Single-field selectors** keep the original native `<select>` as the primary control. Search is supplemental: an XP-style `搜尋` button opens a custom list directly below the control.
3. **Pipeline-aware field references** that must accept generated aliases remain editable text fields. Their exact typed value is valid without clicking a suggestion. A compact XP-style `▼` button and below-control popup provide discovery/search.
4. **No native `<datalist>` UI** is used for field discovery. Browser-native datalist placement and styling are intentionally removed because they conflict with the application's classic desktop UI.
5. **Field-name options and field-value options are separate domains.** A value control never receives the schema field list.
6. **Field legality is schema-driven, not a frontend field-name allowlist.** `/api/meta` publishes capabilities for each runtime field (for example numeric, temporal/trend-orderable, pitch-classification, canonical-pitch-type). Controls request a capability; they do not own copied lists of Statcast column names. New fields that satisfy a capability become eligible automatically.
7. **Every field list is context-scoped.** It may show only fields that actually exist at that point in the pipeline and that are legal for that specific control. It must not fall back to the entire raw Statcast schema when an earlier Aggregate/Project stage has removed fields.
8. **Type-restricted controls are narrowed by schema/output capability.** Clustering features, regression variables, arithmetic operands, percentile values, ordinary numeric aggregates and Cluster Comparison metrics list numeric fields only. Count/group/ID/partition/order controls remain broader where the backend legitimately accepts broader fields.
9. **Field-to-field comparisons are type-compatible.** A compare-field popup is narrowed to fields compatible with the selected left/condition field instead of offering unrelated types.
10. **Baseball-specific field roles come from backend schema capabilities.** Relative pitch selectors request the canonical-pitch-type capability; Arsenal Signature requests pitch-classification. The frontend does not hard-code `pitch_type` / `pitch_name` allowlists.
11. **Pipeline output shape is tracked.** Aggregate replaces the available field set with group keys plus metric aliases; Project keeps only explicitly projected fields; Derived/Rolling/Lag-Lead/Trend/Rank/Arsenal/Relative-Pitch/Percentile/Event-Cohort stages add their generated aliases with inferred capabilities.
12. **Numerical setup follows input preparation.** Clustering, Regression and Bootstrap field lists are based on the fields remaining after all Input Preparation stages, including generated aliases.
13. **Semantic value lists are field-dependent.** For example, when the condition field is `pitch_type`, the value popup contains only pitch types; it cannot offer release speed, pitcher, game ID, or any other unrelated field/value.
14. Finite semantic domains get curated options (pitch type, batter/pitcher handedness, count state, zone, inning half, event/description categories, team code, etc.). Open-ended identifiers/numeric fields remain free text rather than pretending to have a complete list.
15. `IN` / `NOT IN` and explicitly multi-value controls append multiple semantic values; single-value comparisons replace the current value.

## Audited control families

### Existing/core analysis pages
- Common Filters: field selector + context-dependent value.
- Sequence Pattern: event field + event value.
- Follow-up Event: anchor field/value, target field/value, between field/value.
- Basic Analysis: metric field is narrowed by aggregate requirements; Group By remains the approved checkbox-list implementation.
- Pitch Arsenal / Pitch Role / Temporal / Percentile / Cross-Level / Arsenal Change: single field selectors restored as selects; entity/unit/baseline multiple selectors remain checkbox lists.
- Pitch Role selected-data field is narrowed according to aggregate semantics; excluded pitch types use the semantic pitch-type value list.
- Result Ordering uses only the actual output fields exposed by that analysis mode.

### Advanced Research workflow
- Group By, metric field, conditional field, derived left/right field, filter field / compare field, rolling field, partition/order fields, lag/lead field, trend field, rank/project/sort fields.
- Generated aliases from preceding stages remain discoverable in pipeline-aware field popups.
- Fields removed by Aggregate or Project disappear from every downstream list immediately.
- Numeric-only workflow operations exclude text/ID fields where arithmetic/statistical execution requires numeric input.
- Compare-field lists are narrowed to fields compatible with the selected source field.
- Conditional constant values are semantic when the referenced field has a finite domain.
- Event Pattern Cohorts event value is tied to its event field; arrangement choices are their own finite domain.
- Arsenal Signature / Relative Pitch Selector / Relative Pitch Annotation / Empirical Percentile field references are covered.
- Pitch-field controls consume backend semantic capabilities rather than frontend name lists.

### Numerical / multi-stage analysis
- Clustering features are numeric; IDs and partition fields are chosen from the fields actually available after Input Preparation.
- Regression dependent/independent fields are numeric and use the post-preparation pipeline shape.
- Bootstrap mean/median value fields are numeric; proportion may use categorical values; resampling-unit and group fields use post-preparation fields. Group A/B and success values become semantic when their referenced field has a finite domain.
- Cluster Comparison entity/features fields, common-filter field/value, selection/evaluation field selects, and reference pitch are independently scoped; selection/evaluation/features are numeric while reference-pitch values use the pitch-type semantic value domain.

## Deliberate non-lists

A value box remains free-form when the application cannot provide a small, semantically complete domain without querying potentially millions of rows. Examples include player IDs, game IDs, raw continuous measurements, and arbitrary numeric thresholds. This is preferable to showing unrelated or misleading suggestions.

The same principle applies to field lists: when runtime schema metadata is temporarily unavailable, the UI stays permissive rather than falsely hiding legal fields. Once `/api/meta` is available, capability-specific narrowing is applied automatically.
