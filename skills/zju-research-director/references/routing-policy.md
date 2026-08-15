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
| Bulk omics screen (optional pack) | experiment log/design metadata -> omics analysis -> statistics/figure handoff |
| Materials structure or calculation (optional pack) | chemistry identity as needed -> materials computation -> experiment log/statistics/figure handoff |
| Drug-discovery candidate triage (optional pack) | literature/chemistry evidence -> drug discovery -> hypothesis design -> statistics/integrity challenge |

These are starting recipes. Skip an upstream step only when its required artifact is supplied and validated; record the reused artifact ID. Add chemistry database routing, monitoring, data availability, or integrity review only when the mission requires them.

Optional-pack recipes are eligible only when the corresponding `$zju-omics-analysis`, `$zju-materials-computation`, or `$zju-drug-discovery` directory is installed beside the Director. `load_registry` ignores absent optional entries. Do not replace an unavailable pack with a similarly named core Skill or claim that installing a pack installs its external scientific software.

`plan_mission.py` freezes the normative portion of this policy into a canonical `route_contract` and `route_contract_sha256`. Release rederives that contract from `requested_deliverables`; editing the live route, required output groups, gates, step states, or produced artifact IDs invalidates any earlier release authorization. A skipped required step still needs trusted-validated reused artifacts for every canonical output group.

## Capability selection

1. Match requested deliverables to the `produces` fields in `capability-registry.yaml`.
2. Add prerequisites only when their accepted artifact is not already available and validated.
3. Reject unknown and forbidden capabilities. If no registered skill can produce an output, create a blocking open loop instead of improvising a capability.
4. Prefer the narrowest specialist. The director owns state, dependencies, and gates; it does not replace specialist reasoning.
5. Each step must declare expected output types, prerequisite step IDs, required gates, autonomy level, and validation route.

## Execution-provider binding

Keep mission routing separate from host execution. The capability registry decides *which ZJU specialist* owns a stage; `executor-registry.yaml` describes optional concrete providers for eight logical runtime capabilities. Do not write Codex-, Claude Code-, or OpenCode-specific command syntax into a mission.

Before a ready specialist calls a host capability, obtain an explicit inventory such as:

```json
{
  "platform": "codex",
  "available": [
    "script.zju.paper_reader.prepare_source",
    "tool.document.pdf"
  ]
}
```

Resolve one or more requirements with `scripts/resolve_executor.py`. A structured requirement may constrain accepted inputs or required outputs:

```json
{
  "requirements": [
    {
      "requirement_id": "REQ-PDF-01",
      "capability": "pdf_extraction",
      "accepts": ["local_pdf"],
      "produces": ["reader_source_bundle"]
    }
  ]
}
```

Selection is deterministic: reject unavailable, platform-incompatible, and I/O-incompatible candidates; prefer a narrow compatible provider over a generic provider, then use descending priority and provider ID as tie-breakers. The resolver does not inspect the machine or execute anything. An unavailable or unknown capability remains `unresolved`; do not silently replace it with prose generation. After host execution, apply the selected provider's output adapter and validate the resulting artifact through the normal stage contract.

## State transfer packet

Pass only the task-local subset needed by a specialist:

- mission and step IDs;
- bounded objective and mode;
- accepted artifact/evidence IDs and source paths;
- required output types and schema;
- constraints, autonomy ceiling, human gates, and stop conditions;
- known risks and unresolved inputs that affect this step.

Freeze specialist outputs before downstream use. Every cross-skill handoff must satisfy `artifact-envelope.schema.yaml`: merge by stable ID, bind validation to the exact content SHA-256, require mission-matching provenance and a producer-declared output type, and use a new ID plus `supersedes` for a revision. Local report consistency is not execution proof. Release-quality handoffs additionally require the separate trusted-runner attestation contract; an attestation copied into mutable mission state is ignored.

## Resume semantics

For a schema `1.0` mission, first run `migrate_mission.py` with an explicit timezone-aware migration timestamp. The migration preserves old validation metadata only as `legacy_validation.effective: false`, downgrades its active status, and does not read files or create hashes. Then validate the whole `1.1` mission, preserve completed/failed history, recalculate readiness from dependency and gate state, and select the earliest ready incomplete step. Never restart completed work unless its input was superseded or a correction/retraction invalidated it.
