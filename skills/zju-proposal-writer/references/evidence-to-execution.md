# Evidence-to-execution handoff

Use this contract when `$zju-evidence-synthesis` or `$zju-hypothesis-design` feeds a proposal. Preserve upstream IDs; do not turn an evidence gap into a project fact.

## Input mapping

| Upstream artifact | Proposal field | Transformation rule |
|---|---|---|
| Claim/evidence row | `evidence[]` | Preserve `evidence_id`, status/certainty, source anchor, and scope. Record what the row supports and what it cannot support. |
| Competing hypothesis | `hypotheses[]` | Preserve hypothesis ID, statement, predictions, falsifiers, assumptions, and evidence IDs. Keep alternatives in the same decision problem. |
| Discriminating experiment | `experiments[]` | Preserve experimental unit, intervention/comparator, method, outcomes, decision rules, feasibility needs, and hypothesis IDs. |
| Evidence conflict or gap | WP task or decision branch | Convert to a bounded resolution task. Do not describe the gap as preliminary support. |
| Preliminary result | Feasibility evidence | Preserve provenance, conditions, result ID, uncertainty, and whether it was prospective or exploratory. |

The structured handoff accepted by `validate_proposal_manifest.py --compile-handoff` contains:

```yaml
proposal_id: P-001
mode: compose
scheme_status: template_pending | official_verified
evidence:
  - evidence_id: EV-001
    status: verified
    source_anchor: "doi:...#figure-2"
hypotheses:
  - hypothesis_id: H1
    statement: ""
    evidence_ids: [EV-001]
    predictions: [""]
    falsifiers: [""]
objectives:
  - objective_id: O1
    question: ""
    hypothesis_ids: [H1, H0]
    evidence_ids: [EV-001]
    success_criteria: [""]
experiments:
  - experiment_id: EX1
    objective_ids: [O1]
    hypothesis_ids: [H1, H0]
    evidence_ids: [EV-001]
    inputs: [""]
    experimental_unit: ""
    methods: [""]
    outputs: [""]
    capability_ids: [CAP-001]
    depends_on_experiment_ids: []
    owner: ""
    start_month: 1
    end_month: 6
    milestone_id: M1
    milestone_acceptance: ""
    decision_rule:
      metric: ""
      branches:
        success: {condition: "", next: advance}
        inconclusive: {condition: "", next: repeat_or_redesign}
        failure: {condition: "", next: pivot_or_stop}
capabilities:
  - capability_id: CAP-001
    item: ""
    dimension: scientific | data_sample | method | instrument | personnel | time | budget | ethics | collaboration
    status: available | partial | missing | unverified
    evidence_ids: [EV-001]
    constraint: ""
    mitigation: ""
risks: [{risk_id: R1}]
compliance: [{item: template_pending}]
submission_ready: false
```

## Compilation rules

1. Create one work package per supplied discriminating experiment; merge only when experiments share the same decision gate and output.
2. Copy evidence, hypothesis, objective, experiment, result, and capability IDs unchanged.
3. Express every gate with `success`, `inconclusive`, and `failure` branches. A milestone is not merely a date; it needs a deliverable and acceptance criterion.
4. Order work packages from explicit experiment dependencies. A cycle is a planning error.
5. Build the feasibility matrix at the work-package/capability level. `available` means evidence-backed access; `partial`, `missing`, and `unverified` require a constraint and mitigation.
6. Treat the compiler output as a proposal-manifest draft. Scientific wording and scheme compliance still require review.

The compiler returns the proposal manifest plus deterministic `objective_work_package_map`, `work_package_sequence`, `milestone_decision_tree`, and `feasibility_matrix`. Use its findings to repair the plan before prose drafting.
