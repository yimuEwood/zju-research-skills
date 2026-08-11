# Canonical result registry

Use one versioned registry as the numerical single source of truth for manuscript outputs. Store machine-readable values, not rounded prose fragments.

```json
{
  "schema_version": "1.0",
  "study_id": "STUDY-001",
  "analysis_contract_id": "AC-001",
  "registry_version": "2026-08-11.1",
  "results": [
    {
      "result_id": "RES-primary-day14",
      "analysis_id": "AN-primary",
      "outcome_id": "OUT-primary",
      "result_kind": "inferential",
      "analysis_population": "all randomized experimental units with observed day-14 outcome",
      "effect_measure": "adjusted mean difference",
      "estimate": -2.4,
      "unit": "mg/L",
      "direction": "lower_in_treatment",
      "ci": {"level": 0.95, "lower": -3.7, "upper": -1.1},
      "p_value": {"operator": "<", "value": 0.002},
      "multiplicity_status": "primary_single_test",
      "n": {"experimental_units": 48, "observations": 48},
      "diagnostics": [{"check": "residual_pattern", "result": "acceptable", "consequence": "none"}],
      "sensitivity_analyses": [{"analysis_id": "AN-primary-robust", "conclusion": "direction_and_material_magnitude_preserved"}],
      "source_anchor": "analysis/output.json#/primary",
      "status": "verified"
    }
  ],
  "uses": [
    {
      "use_id": "USE-F2A",
      "consumer_type": "figure",
      "anchor": "Fig. 2a",
      "result_id": "RES-primary-day14",
      "values": {"estimate": {"rendered": "-2.4", "format": ".1f"}, "unit": "mg/L", "p_value": {"operator": "<", "value": 0.002}, "n.experimental_units": 48}
    }
  ]
}
```

Run `scripts/reconcile_result_registry.py --analysis-contract analysis-contract.json` before delivery. Every `analysis_id` and `outcome_id` must exist in that contract, and an outcome must belong to the referenced analysis. Represent an exact P value as a number and a bounded value as `{"operator": "<", "value": 0.001}`. A `use` may report a subset of canonical fields. Store exact values directly; store a rounded display as `{"rendered": "-2.4", "format": ".1f"}` so the validator can reproduce the rounding. Create a new result version after reanalysis; never silently edit a released value.

For machine-generated text, legends, or tables, use tokens such as `{{result:RES-primary-day14:estimate|.1f}}`, `{{result:RES-primary-day14:ci.lower|.1f}}`, and `{{result:RES-primary-day14:n.experimental_units}}`, then run `scripts/render_result_tokens.py` with `--consumer-type` and `--anchor`. Its output contains standard `use_id`, `consumer_type`, `anchor`, `result_id`, and `values` records that can be appended directly to the registry and revalidated.
