# Executable one-panel figure specification

`render_from_registry.py` turns a local table and one verified result-registry record into a real PNG, SVG, and/or PDF plus `figure-manifest.json`. It is a narrow reproducible renderer, not a general automatic figure designer.

## Shared fields

```json
{
  "figure_id": "FIG-1",
  "bounded_conclusion": "Response increases over the measured dose range.",
  "result_id": "RES-slope",
  "analysis_id": "AN-slope",
  "plot_type": "scatter_regression",
  "experimental_unit_column": "sample_id",
  "experimental_unit": "independent preparation",
  "width_mm": 89,
  "height_mm": 70,
  "dpi": 300,
  "formats": ["png", "svg", "pdf"]
}
```

`result_id` must resolve to exactly one `verified` result. `analysis_id`, when present, must match it. Formats must be a unique subset of `png`, `svg`, and `pdf`. The default size is 89 × 70 mm at 300 DPI for PNG; vector exports do not receive a fake DPI claim.

## Plot types

- `group_comparison`: add `value_column`, `group_column`, `experimental_unit_column`, `reference_group`, `comparison_group`, `group_order`, `x_label`, and `y_label`. `group_order` must be `[reference_group, comparison_group]`. The plot shows individual observations plus each group's arithmetic mean and 95% t confidence interval. The linked result remains the canonical between-group contrast.
- `paired_comparison`: use the same fields with exactly two conditions; `group_column` names the contract's `condition_column`. The plot shows complete pairs connected by lines and does not treat incomplete pairs as complete.
- `scatter_regression`: add `x_column`, `y_column`, `x_label`, and `y_label`. If the canonical result was produced by `simple_ols`, the stored slope and intercept determine the fitted line. Pearson results produce points without an invented regression line.
- `multi_group_comparison`: bind `value_column`, `group_column`, `experimental_unit_column`, and the exact ANOVA `group_order`. It shows all observations plus per-group means and 95% t intervals while retaining the omnibus result as canonical.
- `logistic_curve`: bind `outcome_column`, `predictor_column`, `experimental_unit_column`, `event_value`, and `non_event_value`. The stored GLM slope and intercept determine the probability curve; observed binary values remain visible.

The renderer uses an Okabe-Ito palette, hides nonessential top/right spines, embeds editable text in SVG/PDF when supported, hashes every input and export, and records the exact analysis/result IDs. When the registry contains `execution_provenance.data_sha256`, the data file must match it exactly. Rendered experimental-unit and observation counts must also match canonical `n`; a mismatch stops export rather than drawing a different dataset under the same `result_id`. It does not perform grayscale simulation, semantic visual inspection, or a full data-to-mark audit; those remain explicit QA tasks.
