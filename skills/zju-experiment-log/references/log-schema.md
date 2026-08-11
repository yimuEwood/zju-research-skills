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
