# Monitoring profile

Use one immutable version per substantive query change.

```yaml
profile_id: MON-001
profile_version: 1
status: draft | active | paused | retired
research_question: "..."
concepts:
  required: []
  optional: []
  excluded: []
seed_identifiers: []
sources: []
languages: []
coverage_window: ""
cadence: weekly | monthly | manual
expected_records_per_run: 0
delivery_target: ""
archive_target: ""
created_at: "ISO-8601"
approved_by: ""
```

For each query version record the exact string or structured payload, database, filters, date executed, and reason for change. Never edit an old version in place. A spelling fix that changes retrieval is a new version.

Pause and request confirmation when a scope change affects fields, populations, interventions, outcome families, material classes, or language/date restrictions.

\n