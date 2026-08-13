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

## Quantitative pooling input

Do not pass the general evidence-map rows directly to the pooling helper. Create a prespecified compatible subset with root `effect_measure`, `estimand`, `outcome`, `time_point`, `analysis_scale`, `independent_estimates: true`, `confidence_level`, `tau_squared_estimator`, and `random_effects_inference`, plus an `effects` array. Each effect repeats the compatibility fields and provides a unique `study_id`, numeric `effect_estimate`, and either positive `standard_error` or `ci_lower` plus `ci_upper`. This repetition is deliberate: `scripts/pool_effects.py` rejects a row when its estimand, outcome, effect scale, or time point differs from the requested pool. Ratio effects must be supplied on the log scale and correlations on the Fisher-z scale; the helper returns both analysis-scale and back-transformed estimates, including the prediction interval when estimable.

`reml` is a useful sensitivity-analysis starting point when the between-study variance is not prespecified; `dersimonian_laird` remains available for legacy comparability but can underestimate heterogeneity. `hartung_knapp_modified` prevents the Hartung-Knapp residual scale from shrinking below one, while unmodified `hartung_knapp` preserves the conventional estimator and may occasionally narrow the interval. The script makes the choice explicit; protocol, domain, effect measure, and study count determine whether it is defensible.
