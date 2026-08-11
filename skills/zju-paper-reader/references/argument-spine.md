# Argument spine and evidence handoff

Use this reference for deep Paper Card reading and whenever a paper must feed evidence synthesis or hypothesis design.

## Reconstruct the paper spine

Do not follow page order blindly. Reconstruct these linked nodes:

1. `problem`: what is not explained or enabled before this work?
2. `claim`: the bounded statement the authors need the reader to accept.
3. `method`: the design choice or measurement that can support that claim.
4. `evidence`: the observation, estimate, figure/table, equation, or robustness result.
5. `warrant`: why that evidence supports the claim under the stated assumptions.
6. `alternative`: a competing explanation or artifact.
7. `boundary`: population/system, conditions, scale, time, and measurement limits.
8. `next_test`: an observation that would distinguish the claim from an alternative.

Every central claim needs at least one evidence edge and one boundary or caveat. Separate “authors argue” from “the data show” and from the reader's interpretation.

## Claim-evidence node

```yaml
claim_id: REC-00001-C01
record_id: REC-00001
claim_text: ""
claim_type: descriptive | associational | causal | mechanistic | predictive
author_status: central | secondary | speculative
evidence:
  - evidence_id: REC-00001-E01
    evidence_type: experiment | observation | model | simulation | prior_literature
    source_anchor: "p. 6, Results, Fig. 3b"
    result: ""
    uncertainty: ""
warrant: ""
assumptions: []
alternative_explanations: []
boundary_conditions: []
reader_assessment: supported | partially_supported | unsupported | unclear
```

## Cross-paper-ready `paper-spine.json`

Preserve the upstream `record_id` and output: `source_identity`, `coverage`, `claims`, `evidence_items`, `methods`, `figures`, `tables`, `equations`, `contradictions`, `limitations`, `reproducibility_assets`, and `handoff`.

The handoff must contain:

- `synthesis_claim_ids` and evidence rows suitable for `$zju-evidence-synthesis`;
- `candidate_gap_ids`, unresolved alternatives, and discriminating observations for `$zju-hypothesis-design`;
- terms, citations, corrections, or unresolved source components that `$zju-literature-search`, `$zju-fulltext-access`, or `$zju-literature-monitor` should revisit.

Do not convert a source-level judgment into cross-study certainty. The paper spine describes one report; synthesis determines how it fits the wider evidence base.
