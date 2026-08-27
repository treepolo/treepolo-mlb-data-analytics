# Field Control Audit

Date: 2026-08-28

This audit defines the field-control contract after UI acceptance found two separate failure modes: browser/native search UI broke the Windows XP-style interaction, and later the restored native single-select plus separate `搜尋` button still split one field choice into two controls. The accepted design uses one shared legality provider and one editable combo interaction for every large field-name chooser.

## Interaction rules

1. **Multiple-field checkbox selectors** keep the existing checkbox-list UI, search box, and selected-item summary. They are not converted to text inputs.
2. **Large single-field selectors are one editable combo control.** The visible row both displays the current field and accepts typing. Clicking/focusing that same row opens the legal option list directly below it. Typing filters that list. Typing an exact legal field name or exact displayed label commits the selection immediately; Enter or an extra click is not required.
3. **There is no separate field-search button or second search input.** Search and list opening belong to the editable field row itself. Editable/list-backed controls display the same browser-native dropdown arrow as ordinary legacy `<select>` controls by overlaying a clipped, non-interactive real `<select>` as an arrow donor. The arrow is not recreated with CSS lines or a text glyph.
4. **Pipeline-aware single-field references use the same interaction.** They remain editable because they may reference aliases created by earlier stages, but the current input itself is also the search input and legal-choice popup owner.
5. **Pipeline-aware multi-field references** remain comma-separated editable inputs where required by the backend contract. Their candidate list is supplied by the same legality provider and reflects the current pipeline shape.
6. **Small fixed-option selects remain ordinary legacy selects.** Operators, aggregation choices, direction/method choices and similar short enumerations are not data-field selectors and do not receive field search behavior.
7. **Alias/name creation inputs remain plain text inputs.** Controls such as metric aliases, derived-field aliases, custom aliases and cohort aliases create new names; they are not field choosers and must not be decorated as one.
8. **Field-name options and field-value options are separate domains.** A value control never receives the schema field list. Finite semantic values such as pitch type or handedness use their own domain; open-ended values remain free text.
9. **No native `<datalist>` field-discovery UI is used.** Browser-native datalist placement and styling are intentionally removed.
10. **Every actual field-name chooser goes through the same legality path.** A control with no additional semantic/type restriction still asks the legality provider for the fields that exist at its current data/pipeline point. “All fields are legal here” is therefore the broadest result of the same system, not a separate bypass implementation.
11. **Result Ordering is output-schema scoped.** Its field choices come from the current analysis output fields/computed results rather than the raw input schema; it uses the same editable-combo interaction while retaining that distinct legality source.
12. **Field legality is schema-driven, not a frontend field-name allowlist.** `/api/meta` publishes capabilities for each runtime field, for example numeric, temporal/trend-orderable, pitch-classification and canonical-pitch-type. Controls request capabilities instead of owning copied lists of Statcast column names.
13. **Every field list is context-scoped.** It may show only fields that actually exist at that point in the pipeline and that are legal for that control. It must not fall back to the entire raw Statcast schema when an earlier Aggregate/Project stage has removed fields.
14. **Type-restricted controls are narrowed by schema/output capability.** Clustering features, regression variables, arithmetic operands, percentile values, ordinary numeric aggregates and Cluster Comparison metrics list numeric fields only. Count/group/ID/partition/order controls remain broader where their backend contracts legitimately accept broader fields.
15. **Field-to-field comparisons are type-compatible.** Compare-field choices are narrowed to fields compatible with the selected source/condition field.
16. **Baseball-specific field roles come from backend schema capabilities.** Relative Pitch selectors request the canonical-pitch-type capability; Arsenal Signature requests pitch-classification. The UI does not own `pitch_type` / `pitch_name` allowlists.
17. **Pipeline output shape is tracked.** Aggregate replaces the available field set with group keys plus metric aliases; Project keeps only explicitly projected fields; Derived/Rolling/Lag-Lead/Trend/Rank/Arsenal/Relative-Pitch/Percentile/Event-Cohort stages add their generated aliases with inferred capabilities.
18. **Numerical setup follows Input Preparation.** Clustering, Regression and Bootstrap field choices are based on the fields remaining after all Input Preparation stages, including generated aliases.
19. **Semantic value lists are field-dependent.** When a condition field is `pitch_type`, the value choices contain pitch types; when it is `stand`, the choices are L/R. Open-ended identifiers and continuous numeric values remain free text.
20. `IN` / `NOT IN` and explicitly multi-value semantic controls can contain multiple values; single-value comparisons commit one value.

## Shared implementation boundary

The field UI and field legality have separate responsibilities:

- `field-option-legality-v3.js` derives the legal field set from runtime schema capabilities, current pipeline/output shape and the control’s semantic contract.
- `field-controls-unified.js` owns the interaction/rendering for field-name controls and finite semantic-value controls. It does not maintain per-control field allowlists.
- `field-controls-native-arrow.js` inserts a non-interactive real `<select>` into each list-backed editable control, and `field-controls-native-arrow.css` clips that donor to its right-side native arrow region. This intentionally reuses the browser/OS select renderer instead of approximating the arrow with CSS.
- Dynamic controls are discovered by a shared selector registry plus a DOM observer, so fields created after adding workflow stages, filters, metrics or Cluster Comparison controls receive the same behavior.
- The prior `field-controls-classic.js` implementation remains in repository history but is no longer loaded by the application; it is not a second active field-control path.

This separation is preferred to giving every field its own custom UI code. Per-field implementations would duplicate search/selection logic, make dynamically inserted controls easy to miss, and encourage field-name allowlists to drift from backend contracts.

## Audited control families

### Existing/core analysis pages
- Common Filters: field selector + context-dependent value.
- Sequence Pattern: event field + event value.
- Follow-up Event: anchor field/value, target field/value, between field/value.
- Basic Analysis: metric field is narrowed by aggregate requirements; Group By remains the approved checkbox-list implementation.
- Pitch Arsenal / Pitch Role / Temporal / Percentile / Cross-Level / Arsenal Change: large single field selectors use the editable combo; entity/unit/baseline multiple selectors remain checkbox lists.
- Pitch Role selected-data field is narrowed according to aggregate semantics; excluded pitch types use the semantic pitch-type value domain.
- Result Ordering uses only the actual output fields exposed by that analysis mode.

### Advanced Research workflow
- Aggregate Group By, metric field, conditional field, Derived left/right field, Filter field / compare field, Rolling field, partition/order fields, Lag/Lead field, Trend field, Rank/Project/Sort fields.
- Generated aliases from preceding stages remain discoverable and directly typeable.
- Fields removed by Aggregate or Project disappear from downstream candidate lists.
- Numeric-only workflow operations exclude text/ID fields where arithmetic/statistical execution requires numeric input.
- Compare-field choices are narrowed to fields compatible with the selected source field.
- Conditional constant values are semantic when the referenced field has a finite domain.
- Event Pattern Cohorts event value is tied to its event field; arrangement choices are their own finite domain.
- Arsenal Signature / Relative Pitch Selector / Relative Pitch Annotation / Empirical Percentile field references are covered.
- Alias-creation controls remain plain text and are deliberately excluded from the field-choice registry.

### Numerical / multi-stage analysis
- Clustering features are numeric; IDs and partition fields are chosen from fields actually available after Input Preparation.
- Regression dependent/independent fields are numeric and use the post-preparation pipeline shape.
- Bootstrap mean/median value fields are numeric; proportion may use categorical values; resampling-unit and group fields use post-preparation fields. Group A/B and success values become semantic when their referenced field has a finite domain.
- Cluster Comparison entity/features fields, common-filter field/value, selection/evaluation fields and reference pitch are independently scoped; selection/evaluation/features are numeric while reference-pitch values use the pitch-type semantic value domain.

## Deliberate non-lists

A value box remains free-form when the application cannot provide a small, semantically complete domain without querying potentially millions of rows. Examples include player IDs, game IDs, raw continuous measurements and arbitrary numeric thresholds. This is preferable to showing unrelated or misleading suggestions.

A newly created alias/name is also deliberately free-form because the user is defining a new field name rather than selecting an existing one.

When runtime schema metadata is temporarily unavailable, the legality layer may operate with the currently known field structure; once `/api/meta` is available, capability-specific narrowing is applied automatically. An actually empty legal set must stay empty rather than falling back to the full raw schema.
