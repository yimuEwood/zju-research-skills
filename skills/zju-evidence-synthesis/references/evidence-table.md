# Evidence table contract

Minimum study-outcome row:

```yaml
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

Use `not_reported` rather than an empty string when the source was checked. Distinguish `not_applicable` from `not_reported`. Store converted values alongside the original value and conversion rule.

Every synthesis claim must list contributing study IDs, contradictory study IDs, certainty, and the specific evidence-table fields used.

\n