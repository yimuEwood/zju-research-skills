# Response tracker

Minimum item:

```yaml
comment_id: R1.1
concern_id: R1-M1
action_id: ACT-R1-01
source_role: editor | reviewer_1 | reviewer_2
verbatim_comment: ""
action_type: clarification | manuscript_edit | new_analysis | new_experiment | citation | disagreement | impossible | author_input
requested_action: ""
evidence_status: verified | partial | absent | not_applicable
evidence_ids: []
result_ids: []
response_text: ""
manuscript_change: ""
manuscript_diff:
  before: ""
  after: ""
  dependent_artifacts: []
result_reconciliation:
  status: pending | passed | failed
  registry_version: ""
location: "section/page/line or LOCATION_PENDING"
status: open | in_progress | verified_complete | disagreed_with | author_input_needed
owner: ""
```

Use `verified_complete` only when the action/evidence, response wording, manuscript change, and location agree. A defensible disagreement still needs a clear response and any clarifying manuscript edit.

Use `workflow_version: "2.0"` for the closed-loop form. It requires concern/action IDs, evidence IDs, and an exact manuscript diff for completed actions. A completed new analysis also requires result IDs plus a passed `result_reconciliation` tied to the registry version. `dependent_artifacts` names the Abstract, Results, Methods, figure/table, supplement, data package, or response quotation that must be regenerated or rechecked.
