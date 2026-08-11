# Design-to-analysis handoff

Create this handoff before confirmatory data collection or as soon as a legacy experiment is reconstructed. It is the bridge from the laboratory record to `$zju-statistics-audit`; it does not replace a protocol or preregistration.

## Study design snapshot

Record:

- `study_id`, `experiment_id`, hypothesis/question, domain profile, and design stage;
- experimental unit, observational unit, biological replicate, technical replicate, and how technical replicates will be aggregated;
- factors, groups, controls, assignment/randomization unit, blocking, nesting/clustering, pairing, repeated measures, and time points;
- planned, generated, excluded, analyzed, and plotted counts at the experimental-unit and observation levels;
- primary, secondary, and exploratory outcomes with stable `outcome_id`, measurement method, scale, unit, time point, and blinded assessment status;
- prespecified exclusions, outlier handling, stopping rule, missingness events, protocol deviations, and amendment history.

Use `unknown` for facts that cannot be reconstructed. Distinguish `not_applicable` from `not_recorded`.

## Analysis contract link

Link the record to a versioned analysis contract with:

```yaml
analysis_contract:
  contract_id: AC-001
  path: analysis-contract.json
  sha256: "..."
  status: draft | frozen | amended | not_applicable
  frozen_at: "ISO-8601 or unknown"
  amendment_ids: []
```

The contract should be validated with `$zju-statistics-audit/scripts/validate_analysis_contract.py`. A completed run may produce data without a frozen analysis contract, but it must then be labelled `retrospective_analysis` rather than silently presented as prespecified.

## Artifact roles

Give every downstream artifact a stable ID and one role: `raw_data`, `metadata`, `qc`, `processed_data`, `analysis_output`, `result_registry`, `figure_source`, `table_source`, or `other`. Preserve lineage as `derived_from` artifact IDs. This allows figures, tables, prose, supplements, and availability statements to refer to the same scientific objects instead of copying values by hand.
