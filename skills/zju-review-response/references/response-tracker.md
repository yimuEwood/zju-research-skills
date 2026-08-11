# Response tracker

Minimum item:

```yaml
comment_id: R1.1
source_role: editor | reviewer_1 | reviewer_2
verbatim_comment: ""
action_type: clarification | manuscript_edit | new_analysis | new_experiment | citation | disagreement | impossible | author_input
requested_action: ""
evidence_status: verified | partial | absent | not_applicable
response_text: ""
manuscript_change: ""
location: "section/page/line or LOCATION_PENDING"
status: open | in_progress | verified_complete | disagreed_with | author_input_needed
owner: ""
```

Use `verified_complete` only when the action/evidence, response wording, manuscript change, and location agree. A defensible disagreement still needs a clear response and any clarifying manuscript edit.

\n