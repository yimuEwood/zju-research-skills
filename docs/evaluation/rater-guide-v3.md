# Rater guide for external L3/L4 evaluation

You will receive a packet containing a task, its fixture, gold checks, and responses labelled A, B, and C. The labels do not identify the systems. Do not try to infer them and do not discuss scores with the other primary rater.

For each response:

1. Confirm that the response SHA-256 matches the packet.
2. Score the five fields in `evals/rubric-v3.md` as integers from 0 to 4.
3. Mark every gold check once, using only evidence visible in the response or its bound artifact.
4. Set a critical failure only when the rubric's material-failure rule applies.
5. Save one record shaped like `evals/templates/v3/rating.template.json`, with a unique rating call ID and the exact rubric hash.

The scorer expects these exact dimension names: `task_completeness`, `evidence_traceability`, `scientific_validity`, `integrity`, and `usability`. It recomputes the 0–100 score; handwritten totals do not override the dimension scores.

If adjudication is triggered, the adjudicator rates the same blinded response using the same schema. The administrator then freezes both primary ratings and any adjudication into a layer-specific ratings lock. Allocation must remain hidden until that lock exists.
