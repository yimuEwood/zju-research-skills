# Autonomy levels

Autonomy is a ceiling on actions, not a quality score. A higher level never relaxes evidence, safety, or human-review gates.

| Level | Allowed by default | Still prohibited without a separate gate |
|---|---|---|
| `L0` | Inspect supplied material and advise | File writes, network retrieval, execution, external actions |
| `L1` | Build plans and draft text in the response | Tool execution, persistent writes, external actions |
| `L2` | Read-only retrieval requested by the user, deterministic local scripts, and writes inside the task workspace | Credential persistence, paid actions, remote mutation, submission, legal/ethics decisions |
| `L3` | User-scoped interactive access or compute explicitly authorized for this mission | Unattended external mutation, sharing credentials, bypassing access controls, submission without final approval |
| `L4` | Bounded unattended loops with an explicit objective, time/cost/iteration budgets, stop rules, checkpoint interval, and independent review | Irreversible/high-impact actions and every mandatory human decision |

## Selection rules

1. Default to `L2` for a build request and `L1` for advice or planning only.
2. Preserve the mission ceiling across every child step; a specialist may use a lower level but never a higher one.
3. Treat “continue”, “finish everything”, “all modifications approved”, and similar language as persistence within scope, not as `L3`/`L4` authorization.
4. Require a recorded decision with scope and expiry before an `L3` action. Require explicit budgets, stop conditions, checkpointing, and an independent reviewer before `L4`.
5. Stop before credentials, external messages, remote writes, publication submission, patent filing, participant enrollment, ethics approval, procurement, or spending unless the corresponding human gate is satisfied.

## Required stop conditions

Stop on a critical risk, conflicting source identity, unsupported release claim, missing required artifact, failed validator, exceeded budget, repeated non-progress, a new ethics/privacy concern, or a decision that changes research scope. Record the stop reason and a resumable next action.
