# Multi-field Control Audit

## Rule

Unordered sets of field names use the shared searchable checkbox checklist in `field-checklists.js`. Every checklist asks `treepoloLegalFieldOptions.available(control)` for its current candidates. There is no separate all-fields bypass: contexts where every field is legal simply receive the full legal result.

Ordered field specifications are not converted to checkboxes when order or direction is part of the value. In particular, Research Workflow `Order By` keeps its ordered `field,-field` representation because a plain set of checkboxes cannot preserve sort priority or descending direction.

## Static native multi-selects covered by the shared checklist

- Basic Analysis: Group By
- Pitch Arsenal: Entity Fields
- Pitch Role: Entity Fields
- Temporal Comparison: Entity Fields
- Individual Percentile Threshold: Entity Fields
- Level Comparison: Unit Fields
- Level Comparison: Baseline Fields
- Arsenal Change: Entity Fields

## Dynamic / advanced multi-field controls covered by the same checklist

- Research Workflow: Aggregate Group By
- Research Workflow: Partition By
- Research Workflow: Keep Fields / Project
- Numerical input preparation: the same stage controls above
- Clustering: Features
- Clustering: ID fields
- Clustering: Partition fields
- Regression: Independent variables
- Bootstrap: Resampling unit fields
- Cluster Comparison: Entity fields
- Cluster Comparison: Features
- Arsenal Signature / Relative Pitch stages: Entity fields
- Empirical Percentile: Partition fields

## Deliberate non-checklist multi-field control

- `Order By`: ordered and directional; retains the editable ordered-field control.

## Tie wording

Every UI option whose backend value is `dense_rank` is normalized to:

`保留並列（不跳號） Dense Rank`

The separate `rank` option remains available and unchanged as a distinct ranking method.
