# Review report contract

Every concern needs:

```yaml
concern_id: R1-M1
severity: major | minor
blocking: true | false
axis: ""
claim_pointer: "section/paragraph/figure or not_locatable"
evidence_pointer: "source anchor or missing"
claim_ids: []
evidence_ids: []
result_ids: []
concern: ""
why_it_matters: ""
requested_evidence: []
action_options: []
resolution_test: ""
```

Minor concerns must never be blocking. A missing pointer is an explicit limitation, not permission to invent a line number. Recommendation posture should be conditional and must not masquerade as an editor decision.

For workflow version `2.0`, the ID lists, `requested_evidence`, and `action_options` are required. Phrase a resolution test as an observable pass condition: the analysis/experiment/edit to inspect, the expected artifact or result ID, and what outcome would resolve, narrow, or preserve the concern. This lets the response workflow verify work instead of merely restating promises.

For a blind panel, store the immutable packet hash, reviewer invocation IDs, freeze times, and synthesis time. The synthesis may reconcile concern IDs only after reports are frozen.
