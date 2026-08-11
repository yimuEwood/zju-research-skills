# Hypothesis canvas

Use one row per competing explanation.

```yaml
hypothesis_id: H1
status: candidate | retained | weakened | rejected | unresolved
phenomenon: ""
mechanism: ""
assumptions: []
evidence_ids: []
contradicting_evidence_ids: []
unique_predictions: []
falsifiers: []
boundary_conditions: []
alternative_ids: []
artifact_checks: []
```

A useful prediction names the measured variable, direction or pattern, context, and time point. A falsifier must be a plausible observation, not “complete proof that the theory is wrong.” Do not use “novel,” “important,” or “plausible” as evidence. Record a hypothesis as `unresolved` when available tests cannot distinguish it from alternatives.

For a v2 experiment, add `predicted_outcomes` keyed by every compared hypothesis ID, plus `inconclusive_region`, `feasibility` components from 0 to 1, `cost_level` and `time_level` from 1 to 5, and `orthogonal_measurement`. These fields support transparent research-priority scoring; they do not estimate truth.
