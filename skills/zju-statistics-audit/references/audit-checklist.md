# Statistics audit checklist

## Design and replication

- State primary outcome and estimand.
- Identify experimental unit, observational unit, biological replicate, and technical replicate.
- Verify independence, pairing, blocking, nesting, clustering, repeated measures, randomization, blinding, and allocation.
- Reconcile planned, enrolled/generated, excluded, analyzed, and plotted sample counts.
- Inspect sample-size rationale or precision/power analysis and all assumptions.

## Analysis

- Match outcome scale and distribution to the model.
- Check transformations, normalization, covariates, interactions, time structure, censoring, and model hierarchy.
- Check residual/model assumptions and what was done when they failed.
- Define the multiplicity family and correction/control method.
- Audit missing data, exclusions, outliers, and sensitivity analyses.
- Require effect estimate, direction, units, and uncertainty; P values alone are incomplete.

## Reporting

- Report exact group-wise `n` and define what `n` counts.
- Define center and error bars in every figure.
- State test/model, sidedness, multiplicity method, software/version, and exact or bounded P values.
- Keep abstract, methods, results, figures, captions, tables, and supplement consistent.
- Distinguish statistical, practical, and scientific importance.

## Common critical defects

Pseudoreplication, unit-of-analysis error, unmodeled repeated measures, outcome switching, undisclosed exclusions, incompatible denominator, impossible confidence interval/P-value combination, uncorrected broad multiplicity, and claim direction conflicting with results.

\n