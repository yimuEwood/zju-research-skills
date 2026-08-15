# Fast Mode

Fast Mode is the low-overhead path for one bounded output owned by one specialist. It is not a smaller Research Mission and creates no resumable state.

## Eligible

- exactly one registered requested output;
- one unambiguous specialist owns that output;
- supplied inputs are sufficient for the specialist's fallback or normal contract;
- no multi-stage dependency, submission/release decision, external write, credential use, ethics, patent/legal, privacy, or integrity adjudication is requested.

## Packet

Pass only:

- request ID and objective;
- selected specialist and requested output;
- supplied input types and local artifact references;
- constraints and stop conditions;
- applicable human gates and unresolved prerequisites.

Do not manufacture missing prerequisite artifacts. A specialist may return its registered fallback state.

## Escalate

Switch to `plan` or `execute` when the task needs a second specialist, persistent history, a dependency DAG, repeated monitoring, cross-artifact consistency, a human gate, or a release-readiness claim.
