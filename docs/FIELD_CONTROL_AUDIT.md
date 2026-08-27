# Field Control Audit

Date: 2026-08-27

This audit follows the UI acceptance finding that searchable field controls must preserve the application's classic Windows XP-style interaction instead of replacing native selects with browser-native `datalist` text boxes.

## Interaction rules

1. **Multiple-field selectors** keep the existing checkbox-list UI, search box, and selected-item summary. They are not converted to text inputs.
2. **Single-field selectors** keep the original native `<select>` as the primary control. Search is supplemental: an XP-style `搜尋` button opens a custom list directly below the control.
3. **Pipeline-aware field references** that must accept generated aliases remain editable text fields. Their exact typed value is valid without clicking a suggestion. A compact XP-style `▼` button and below-control popup provide discovery/search.
4. **No native `<datalist>` UI** is used for field discovery. Browser-native datalist placement and styling are intentionally removed because they conflict with the application's classic desktop UI.
5. **Field-name options and field-value options are separate domains.** A value control never receives the schema field list.
6. **Semantic value lists are field-dependent.** For example, when the condition field is `pitch_type`, the value popup contains only pitch types; it cannot offer release speed, pitcher, game ID, or any other unrelated field/value.
7. Finite semantic domains get curated options (pitch type, batter/pitcher handedness, count state, zone, inning half, event/description categories, team code, etc.). Open-ended identifiers/numeric fields remain free text rather than pretending to have a complete list.
8. `IN` / `NOT IN` and explicitly multi-value controls append multiple semantic values; single-value comparisons replace the current value.

## Audited control families

### Existing/core analysis pages
- Common Filters: field selector + context-dependent value.
- Sequence Pattern: event field + event value.
- Follow-up Event: anchor field/value, target field/value, between field/value.
- Basic Analysis: metric field; Group By remains the approved checkbox-list implementation.
- Pitch Arsenal / Pitch Role / Temporal / Percentile / Cross-Level / Arsenal Change: single field selectors restored as selects; entity/unit/baseline multiple selectors remain checkbox lists.
- Pitch Role excluded pitch types: semantic pitch-type list.
- Result Ordering: searchable field select without replacing the select.

### Advanced Research workflow
- Group By, metric field, conditional field, derived left/right field, filter field / compare field, rolling field, partition/order fields, lag/lead field, trend field, rank/project/sort fields.
- Generated aliases from preceding stages remain discoverable in pipeline-aware field popups.
- Conditional constant values are semantic when the referenced field has a finite domain.
- Event Pattern Cohorts event value is tied to its event field; arrangement choices are their own finite domain.
- Arsenal Signature / Relative Pitch Selector / Relative Pitch Annotation / Empirical Percentile field references are covered.
- Relative-pitch exclusion controls use pitch types only.

### Numerical / multi-stage analysis
- Clustering features, IDs, partition fields.
- Regression dependent/independent fields.
- Bootstrap value, resampling-unit and group fields; group A/B and success values become semantic when their referenced field has a finite domain.
- Cluster Comparison entity/features fields, common-filter field/value, selection/evaluation field selects, and reference pitch (pitch types only).

## Deliberate non-lists

A value box remains free-form when the application cannot provide a small, semantically complete domain without querying potentially millions of rows. Examples include player IDs, game IDs, raw continuous measurements, and arbitrary numeric thresholds. This is preferable to showing unrelated or misleading suggestions.
