# Routing policy

## Stage order

Use this partial order unless the mission supplies a justified alternative:

`framing -> discovery -> access -> reading -> synthesis -> design -> execution -> analysis -> communication -> review -> sharing/commercialization -> integrity/release`

Parallelize steps only when neither consumes the other's outputs. Keep explicit dependency edges even when execution is sequential.

## Minimal routing recipes

| Requested result | Core route |
|---|---|
| Reproducible literature set | literature search |
| Full-text paper card | lawful access -> paper reader |
| Evidence synthesis | literature search -> lawful access/reader as needed -> evidence synthesis |
| Hypotheses or experiment design | evidence synthesis -> hypothesis design |
| Raw data to manuscript package | experiment log -> statistics audit -> scientific figure/reference audit -> scientific writing -> reviewer/integrity challenge |
| Paper to group-meeting deck | paper reader -> paper2ppt |
| Proposal | literature search -> evidence synthesis -> hypothesis design -> proposal writer -> reference audit |
| Patent triage or disclosure aid | paper reader/prior-art search as needed -> paper-to-patent -> patent/legal human gate |
| Retraction or correction update | literature monitor -> reference audit -> downstream evidence/claim review |
| Submission package | statistics/reference/writing/figure checks -> reviewer -> integrity -> data availability -> release gate |

These are starting recipes. Skip an upstream step only when its required artifact is supplied and validated; record the reused artifact ID. Add chemistry database routing, monitoring, data availability, or integrity review only when the mission requires them.

## Capability selection

1. Match requested deliverables to the `produces` fields in `capability-registry.yaml`.
2. Add prerequisites only when their accepted artifact is not already available and validated.
3. Reject unknown and forbidden capabilities. If no registered skill can produce an output, create a blocking open loop instead of improvising a capability.
4. Prefer the narrowest specialist. The director owns state, dependencies, and gates; it does not replace specialist reasoning.
5. Each step must declare expected output types, prerequisite step IDs, required gates, autonomy level, and validation route.

## State transfer packet

Pass only the task-local subset needed by a specialist:

- mission and step IDs;
- bounded objective and mode;
- accepted artifact/evidence IDs and source paths;
- required output types and schema;
- constraints, autonomy ceiling, human gates, and stop conditions;
- known risks and unresolved inputs that affect this step.

Freeze specialist outputs before downstream use. Merge new artifacts by stable ID, retain provenance and hashes when available, and use a new ID plus `supersedes` for a revision.

## Resume semantics

Validate the whole mission, preserve completed/failed history, recalculate readiness from dependency and gate state, then select the earliest ready incomplete step. Never restart completed work unless its input was superseded or a correction/retraction invalidated it.
