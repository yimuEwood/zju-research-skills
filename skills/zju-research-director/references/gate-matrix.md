# Gate matrix

A gate returns `passed`, `blocked`, or `requires_human_review`. Warnings never convert a blocker into a pass.

| Gate | Minimum machine-checkable evidence | Blocking conditions | Human authority |
|---|---|---|---|
| `intake` | Question, objectives, domain, deliverables, constraints | Missing decision-critical scope or invalid mission | Research owner for scope changes |
| `fulltext` | Lawfully obtained full-text artifact with source/access provenance | Abstract/snippet used as full paper; access state unknown | User performs credentialed library access |
| `evidence` | Stable evidence IDs, source locators, retrieval provenance, anchors | No evidence; unresolved identity; retracted source treated as active | Domain judgment for contested evidence |
| `data` | Data/run artifact, experimental unit, provenance, exclusions/missingness state | Missing raw/derived linkage, units, sample identity, or design facts | Data owner for exclusions or destructive transforms |
| `analysis` | Declared method/model, inputs, outputs, diagnostics, uncertainty | Failed validator, incompatible design, unresolved critical statistical issue | Responsible analyst for model choice where ambiguous |
| `reference` | Field-level identity and claim-support audit | Citation identity or support unresolved for a release claim | Author resolves ambiguous source choice |
| `claim` | Claim ledger with evidence IDs and anchors | Supported claim lacks evidence/anchor; certainty exceeds source | Author owns scientific interpretation |
| `artifact` | Artifact exists, type/status/provenance declared, required validator passed | Planned-only, missing, conflicting ID, or uninspected deliverable | Owner accepts non-critical presentation choices |
| `ethics` | Recorded approval/waiver ID, scope, status, and pre-start condition when applicable | Approval absent, expired, out of scope, or implied by the agent | Authorized ethics body; never the agent |
| `privacy` | Data classification, lawful basis/consent, minimization, access/export plan | Sensitive data handling unresolved or unauthorized disclosure | Data controller/authorized institutional route |
| `external_action` | Scoped authorization decision, target, action, expiry, rollback/stop rule | Credential capture, unapproved remote mutation, messaging, purchase, or compute | User or designated system owner |
| `patent_legal` | Confidentiality/disclosure ledger and professional review decision | Inventorship inferred from authorship; jurisdiction/timing unresolved; filing advice claimed | Patent professional/technology-transfer office |
| `integrity` | Provenance links, unresolved-risk ledger, contribution/disclosure state | Critical integrity risk open; original evidence not preserved | Formal bodies determine misconduct, not the skill |
| `reproducibility` | Data/code/material inventory, environment/version, access route, limitations | Claim-supporting artifact cannot be located or recreated as declared | Research owner accepts justified restrictions |
| `submission_release` | All applicable upstream gates plus explicit release decision | Missing evidence/artifact, open critical risk, incomplete human gate, or planned work stated as complete | Corresponding author/PI or authorized submitter |

## Evaluation order

For a release, evaluate `evidence -> data/analysis -> reference -> claim -> artifact -> integrity -> reproducibility -> submission_release`. Apply ethics, privacy, external-action, and patent/legal gates whenever triggered, regardless of stage.

## Gate ledger fields

Record `gate`, `status`, `checked_inputs`, `blockers`, `warnings`, `required_actions`, `human_authority`, and the decision/artifact IDs used. A later pass appends a new decision; it must not erase the prior blocked result.
