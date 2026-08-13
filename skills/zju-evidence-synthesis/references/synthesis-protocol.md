# Synthesis protocol

Freeze these fields before screening:

- review ID and version;
- decision context and synthesis mode;
- structured question such as PICO, PECO, SPIDER, or material-process-property;
- eligible designs, populations/material systems, interventions/exposures, comparators, outcomes, time points, and languages;
- information sources and complete search dates;
- deduplication and screening procedure;
- extraction fields and risk-of-bias method;
- effect measures and quantitative model, if any;
- subgroup, sensitivity, and publication-bias analyses;
- certainty framework;
- amendment ledger.

Rapid-review shortcuts must be named with their likely bias direction. A narrative review still needs explicit source selection and claim anchors.

## Pooling gate

Pool only if the studies address a sufficiently common estimand, the units and time points are compatible, dependence is handled, and effect estimates plus uncertainty are available or lawfully derivable. Otherwise use structured synthesis without meta-analysis.

For a bounded offline pooling step, prepare JSON with root fields `effect_measure`, `estimand`, `outcome`, `time_point`, `analysis_scale`, `independent_estimates: true`, `tau_squared_estimator: reml | dersimonian_laird`, `random_effects_inference: normal | hartung_knapp | hartung_knapp_modified`, and `effects`. Every effect row must repeat the five compatibility fields and include a unique `study_id`, `effect_estimate`, and either `standard_error` or confidence-interval bounds. Use `identity` for additive measures, `log` for ratio measures, and `fisher_z` for correlations; estimates and uncertainty must already be expressed on that scale. Run `python scripts/pool_effects.py effects.json --output pooled-effects.json`.

The helper reports inverse-variance fixed effect, the selected random-effects result, Q, tau-squared, I-squared, prediction interval, structured warnings, and a back-transformed result for log/Fisher-z inputs. REML and Hartung-Knapp improve model choice transparency but are not universally valid defaults. With very few studies, both heterogeneity and prediction limits remain unstable; high I-squared requires compatibility/moderator investigation rather than automatic acceptance of the pooled average. The helper does not handle dependent estimates, meta-regression, zero-cell corrections, publication bias, or scientific pooling eligibility.
