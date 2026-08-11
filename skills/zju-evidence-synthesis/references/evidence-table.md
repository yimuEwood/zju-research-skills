# Evidence table contract

Minimum study-outcome row:

```yaml
record_id: REC-00001
study_id: S001
report_id: R001
citation_id: DOI-or-PMID
design: ""
population_or_system: ""
sample_and_unit: ""
intervention_or_exposure: ""
comparator: ""
outcome: ""
time_point: ""
effect_estimate: null
uncertainty: null
result_direction: benefit | harm | mixed | null | unclear
risk_of_bias: low | some_concerns | high | not_assessable
source_anchor: "page/table/figure/section"
extractor_note: ""
```

`record_id` must be the unchanged identifier from `search-map.json` / `paper-spine.json`; do not mint a new study-local replacement. In an evidence-map bundle, add `claim_id`, `evidence_role`, and `directness`, and keep each claim's supporting/contradicting/contextual study-ID lists exactly aligned with row roles.

Use `not_reported` rather than an empty string when the source was checked. Distinguish `not_applicable` from `not_reported`. Store converted values alongside the original value and conversion rule.

Every synthesis claim must list contributing study IDs, contradictory study IDs, certainty, and the specific evidence-table fields used.
