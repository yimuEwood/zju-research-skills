# Experiment log schema

## YAML frontmatter

Required keys:

```yaml
schema_version: "1.0"
experiment_id: "EXP-..."
title: "..."
project: "..."
operator: "..."
experiment_started_at: "ISO-8601 or unknown"
record_created_at: "ISO-8601"
status: "planned|running|completed|failed|partial"
sample_ids: []
tags: []
source_files: []
```

For the design-aware `2.0` handoff, also include:

```yaml
study_id: "STUDY-..."
design_stage: prospective | frozen | amended | retrospective
experimental_unit: ""
observational_unit: ""
repeats:
  biological_replicate_definition: ""
  technical_replicate_definition: "not_applicable or definition"
  technical_replicate_aggregation: "not_applicable or rule"
randomization:
  assignment: randomized | nonrandomized | not_applicable
  randomization_unit: ""
  method: ""
hierarchy:
  group_structure: ""
  blocking_or_nesting: "not_applicable or structure"
repeated_measures: false
outcome_ids: [OUT-001]
data_lineage:
  - artifact_id: RAW-001
    role: raw_data
    derived_from: []
analysis_contract:
  contract_id: AC-001
  status: draft | frozen | amended | not_applicable
  path: ""
  sha256: ""
```

The builder accepts the older flat replicate, assignment, hierarchy, `analysis_contract_id`, and `artifact_lineage` aliases, normalizes them to this structure, and writes the normalized handoff into both YAML and `Design and analysis handoff`. Keep the narrative sections below unchanged so older readers remain usable.

Each source file entry should contain `path`, `sha256` or `unavailable`, `media_type`, and `note`. Use quoted scalar values when punctuation or non-ASCII text could confuse a YAML parser.

## Markdown sections

1. Objective
2. Materials and samples
3. Procedure
4. Deviations
5. Observations
6. Results
7. Interpretation
8. Anomalies
9. Next actions
10. Source manifest
11. Amendments

Observation is a direct record. Interpretation explains what the observation may mean. Derived values must name the formula, input source, units, and software/version when available.
