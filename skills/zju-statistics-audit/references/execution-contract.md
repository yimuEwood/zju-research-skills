# Bounded analysis execution contract

The offline executor consumes the existing frozen or amended analysis contract. Add one `execution` object to each analysis that should actually run. The executor deliberately supports a small core; an unsupported method is a routing requirement, not permission to approximate it.

## Shared fields

```json
{
  "analysis_id": "AN-primary",
  "outcome_ids": ["OUT-primary"],
  "analysis_population": "all complete independent units in the prespecified groups",
  "effect_measure": "mean difference",
  "multiplicity_family": "primary_family",
  "execution": {
    "method": "welch_ttest",
    "result_id": "RES-primary",
    "experimental_unit_column": "sample_id",
    "confidence_level": 0.95,
    "multiplicity_method": "holm"
  }
}
```

`result_id` should be supplied when downstream artifacts already reference it. `confidence_level` defaults to `0.95`. When more than one executed result belongs to the same `multiplicity_family`, set every member to the same `multiplicity_method`: `holm` or `benjamini_hochberg`. A single-result family is left unadjusted and marked accordingly.

When `analysis_ids` select only part of a contract, the executor refuses to adjust a family if another analysis in that family lacks an execution block. Execute the complete declared family or amend and version the contract first; a selected subset must not be treated as the whole multiplicity family.

## Method-specific fields

| Method | Required execution fields | Canonical estimate |
|---|---|---|
| `welch_ttest` | `value_column`, `group_column`, `experimental_unit_column`, `reference_group`, `comparison_group` | comparison mean minus reference mean |
| `paired_ttest` | `value_column`, `condition_column`, `experimental_unit_column`, `reference_group`, `comparison_group` | mean within-unit comparison minus reference difference |
| `pearson_correlation` | `x_column`, `y_column`, `experimental_unit_column` | Pearson correlation coefficient |
| `simple_ols` | `x_column`, `y_column`, `experimental_unit_column` | unadjusted slope for one continuous predictor |

Optional `unit` records the estimate unit. Use explicit axis/result units such as `mg/L`, `unitless`, or `response units per dose unit`; do not infer one from a column name.

## Input and output behavior

- Accepted data: CSV, TSV, JSON/JSONL row objects, and XLSX when pandas and openpyxl are available. Legacy binary `.xls` is not accepted; convert it to `.xlsx` or CSV explicitly so the parser/runtime is known.
- Missing tokens are excluded only from the variables required by that analysis and are listed in `analysis-run.json`.
- Unpaired analyses reject repeated experimental-unit IDs so technical replicates are not silently counted as independent data.
- Paired analyses use complete pairs and list incomplete pair IDs that were excluded.
- `result-registry.json` is immediately compatible with `reconcile_result_registry.py` and retains an `execution_binding` with the exact value, group/condition, x/y, experimental-unit, and reference/comparison fields used by the method.
- `analysis-run.json` records data, contract, and executor SHA-256 values plus Python, NumPy, and SciPy versions.

The executor verifies arithmetic and lineage. It does not verify randomization, measurement quality, causal identification, model adequacy, or domain interpretation. Inspect the emitted diagnostics and run a justified sensitivity analysis before release.
