# Conflict, heterogeneity, and evidence strength

Use this reference whenever studies disagree, when “mixed evidence” would otherwise be the conclusion, or when synthesis feeds hypothesis design.

## Keep three questions separate

1. **Direction conflict**: do comparable studies point in opposing directions or toward effect versus no effect?
2. **Heterogeneity**: do population/system, intervention, dose, time, design, measurement, or setting differ enough to make effects context-dependent?
3. **Evidence strength**: within each direction, how direct, precise, replicated, and low-bias is the evidence?

Do not vote by paper count. Compare study identity, experimental unit, estimand, effect magnitude/uncertainty, risk of bias, directness, and independence of research groups or datasets.

Run `python scripts/build_conflict_matrix.py evidence-map.json --output conflict-matrix.json` to expose directional groups and candidate moderators. A candidate moderator is a search or hypothesis target, not a demonstrated explanation.

## Claim-level synthesis

For each `claim_id`, record:

- supporting, contradicting, null/mixed, and context-only study IDs;
- outcome and estimand compatibility;
- risk-of-bias and directness strata;
- magnitude/precision pattern, not just significance;
- candidate moderators and whether analyses were prespecified;
- unresolved alternative explanations;
- outcome-specific certainty and reasons;
- a bounded conclusion and the observation that would change it.

## `evidence-map.json` handoff

```yaml
review_id: REV-001
rows: []
claims:
  - claim_id: CLM-001
    statement: ""
    supporting_study_ids: []
    contradicting_study_ids: []
    contextual_study_ids: []
    certainty: very_low | low | moderate | high | not_assessed
    heterogeneity_axes: []
    boundary_conditions: []
    gap_ids: []
conflicts: []
gaps:
  - gap_id: GAP-001
    type: missing_population | missing_method | contradiction | imprecision | indirectness | mechanism
    description: ""
    priority: critical | important | exploratory
hypothesis_handoff: {claim_ids: [], gap_ids: [], unresolved_alternatives: []}
monitor_handoff: {claim_ids: [], gap_ids: [], seed_record_ids: []}
```

Reuse upstream `record_id`, `study_id`, `claim_id`, and source anchors. New IDs must never erase their parents.
