# Analysis decision map

Use this reference after reconstructing the design. It selects a model family and robustness checks from the data-generating structure, not from which test yields significance.

## First decisions

1. Define the estimand: population, outcome, treatment/exposure contrast, time horizon, and summary measure.
2. Identify the experimental unit and every dependence source: blocks, batches, sites, subjects, litters, wells, fields, repeated times, and technical replicates.
3. Classify the outcome: continuous, count, proportion/binomial, ordinal, categorical, time-to-event, spatial, longitudinal, compositional, or high-dimensional.
4. Separate confirmatory analyses from exploratory analyses. Define the multiplicity family before interpreting individual P values.
5. Declare missing-data assumptions and which observations enter each analysis population.

## Design-to-model routing

| Structure | Default starting family | Required checks |
|---|---|---|
| Independent continuous groups | linear model or robust/location model | residual shape, variance pattern, influential points, scale suitability |
| Blocked or paired design | paired contrast or model with block/pair term | pairing integrity, within-pair missingness, carryover if relevant |
| Repeated or longitudinal measures | mixed-effects, GEE, or explicit covariance model | subject/unit random structure, time trend, covariance, informative dropout |
| Nested/clustered units | hierarchical/mixed model or cluster-robust inference | cluster count, variance components, small-cluster behavior |
| Counts/rates | Poisson-family, negative-binomial, hurdle/zero-inflated where justified | exposure/offset, overdispersion, excess zeros, calibration |
| Binary/proportion | binomial-family or appropriate clustered extension | separation, calibration, event counts, link-scale interpretation |
| Time-to-event | survival model matched to estimand | censoring mechanism, proportionality if assumed, competing risks |
| Omics/high-dimensional | domain pipeline plus multiplicity/FDR and validation | preprocessing, batch effects, leakage, feature-selection nesting |
| Spatial/field/agricultural | block/spatial or hierarchical model | plot layout, border effects, spatial correlation, season/site transportability |
| Chemistry/materials batches | model batch, specimen, and repeated instrument readings separately | batch drift, calibration, detection limits, technical replicate aggregation |

This table is a starting point. Report why the chosen model represents the design and estimand.

## Missing data and robustness

- Describe amount, pattern, timing, and reason for missingness by group and outcome.
- Avoid complete-case analysis as an unexplained default. State assumptions for imputation, likelihood, weighting, or sensitivity bounds.
- Prespecify sensitivity analyses that challenge consequential assumptions: exclusion rules, transformation, covariance/random-effects structure, influential units, missing-not-at-random scenarios, batch/site effects, and alternative reasonable estimands.
- Treat diagnostics as model-specific evidence. A named diagnostic without its result and consequence is incomplete.

## Minimum reportable result

For each stable `result_id`, report the analysis ID, outcome/contrast, analysis population, experimental-unit `n`, observation count where different, effect measure and estimate, unit/scale, uncertainty interval, exact or bounded P value when applicable, multiplicity status, diagnostics, sensitivity result, and source artifact. Register it once in `result-registry.json`; figures, tables, text, supplement, and reviewer responses should consume that ID.
