# Review report contract

Every concern needs:

```yaml
concern_id: R1-M1
severity: major | minor
blocking: true | false
axis: ""
claim_pointer: "section/paragraph/figure or not_locatable"
evidence_pointer: "source anchor or missing"
concern: ""
why_it_matters: ""
resolution_test: ""
```

Minor concerns must never be blocking. A missing pointer is an explicit limitation, not permission to invent a line number. Recommendation posture should be conditional and must not masquerade as an editor decision.

For a blind panel, store the immutable packet hash, reviewer invocation IDs, freeze times, and synthesis time. The synthesis may reconcile concern IDs only after reports are frozen.

\n