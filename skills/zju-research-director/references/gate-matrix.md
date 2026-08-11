# Gate matrix

A gate returns `passed`, `blocked`, or `requires_human_review`. Warnings never convert a blocker into a pass.

| Gate | Minimum machine-checkable evidence | Blocking conditions | Human authority |
|---|---|---|---|
| `intake` | Question, objectives, domain, deliverables, constraints | Missing decision-critical scope or invalid mission | Research owner for scope changes |
| `fulltext` | Validated, content-bound full-text artifact plus `lawful_access: true` | Missing/invalid artifact or lawful-access provenance | User performs credentialed library access |
| `evidence` | Stable evidence IDs and source locators; retrieval gaps are reported | No source-identified evidence; retracted source treated as active support | Domain judgment for contested evidence |
| `data` | Validated, content-bound data inventory/run artifact | A self-reported experiment row, created-only artifact, or failed receipt cannot pass | Data owner still reviews units, exclusions, missingness and destructive transforms |
| `analysis` | Validated, content-bound statistics/analysis artifact and no open critical analysis risk | Missing/invalid artifact or open critical risk | Responsible analyst still owns model choice, diagnostics and interpretation |
| `reference` | Validated, content-bound reference-audit artifact | A claim-level boolean cannot replace the audit artifact | Author resolves ambiguous identity and claim support |
| `claim` | Claim ledger with evidence IDs and anchors | Supported claim lacks evidence/anchor; certainty exceeds source | Author owns scientific interpretation |
| `artifact` | Artifact exists inside the validation base directory (CLI default: mission/input file parent); type, producer step, SHA-256 and format checks pass; an independent trusted-runner attestation binds artifact, content, command, validator, exit code and report | Planned-only, missing, path escape, local/self-authored receipt only, missing/untrusted runner attestation, hash mismatch, conflicting ID, or unsupported format | Owner accepts scientific/visual semantics not covered by deterministic checks |
| `ethics` | Mission decision plus a separately supplied, state-bound receipt from an allowed ethics role | Receipt absent, expired, future-dated, out of scope, or present only inside mutable mission state | Authorized ethics body; never the agent |
| `privacy` | Classification/handling decision plus a separately supplied receipt from an allowed privacy role | Sensitive-data handling unresolved, receipt absent/out of scope, or unauthorized disclosure | Data controller/authorized institutional route |
| `external_action` | Scoped decision plus a separately supplied receipt bound to target, action, mission state and expiry | Credential capture, self-approved remote mutation, messaging, purchase, or compute | User or designated system owner |
| `patent_legal` | Confidentiality/disclosure ledger plus a separately supplied professional receipt | Inventorship inferred from authorship; jurisdiction/timing unresolved; filing advice claimed | Patent professional/technology-transfer office |
| `integrity` | Provenance links, unresolved-risk ledger, contribution/disclosure state | Critical integrity risk open; original evidence not preserved | Formal bodies determine misconduct, not the skill |
| `reproducibility` | Data/code/material inventory, environment/version, access route, limitations | Claim-supporting artifact cannot be located or recreated as declared | Research owner accepts justified restrictions |
| `submission_release` | Canonical route contract rederived from requested deliverables, all required non-skipped steps completed, trusted-validated artifacts satisfying every route/release output group, all applicable upstream gates, and a caller-supplied approval receipt bound to route state, exact artifact IDs, role, action and expiry | Route/output-group tampering, incomplete step, self-reported narrow payload, missing trusted-runner attestation, missing artifact, stale/broad/self-authored authorization, open critical risk, or planned work stated as complete | Corresponding author/PI or authorized submitter |

## Evaluation order

For a release, evaluate `evidence -> data/analysis -> reference -> claim -> artifact -> integrity -> reproducibility -> submission_release`. Apply ethics, privacy, external-action, and patent/legal gates whenever triggered, regardless of stage.

Passing an engineering gate means the declared files and records satisfy the implemented contract. It does not certify scientific truth, appropriate statistical interpretation, visual quality, legal sufficiency, or institutional approval. Human-gate receipts and validation-runner attestations are supplied outside mutable mission state. Their local formats are auditable but are not an institutional digital-signature service; the caller is responsible for authenticating both external channels.

## Gate ledger fields

Record `gate`, `status`, `checked_inputs`, `blockers`, `warnings`, `required_actions`, `human_authority`, and the decision/artifact IDs used. A later pass appends a new decision; it must not erase the prior blocked result.
