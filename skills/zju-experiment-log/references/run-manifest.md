# Reproducible run manifest

Create a run manifest whenever an experiment log represents an instrument run, simulation, data-processing step, or code execution. The manifest complements the narrative log; it does not replace raw files or an electronic lab notebook.

## Required fields

- `run_id`, `experiment_id`, `started_at`, `status`
- `inputs`: stable path or identifier plus SHA-256 when accessible
- `parameters`: supplied settings and units; use `unknown` instead of guessing
- `software`: program, version, environment or instrument firmware when known
- `outputs`: expected and observed artifacts with path, identifier, and hash when accessible
- `decision_gate`: criterion, observed value or status, decision, actor, and timestamp

Allowed run status: `planned`, `running`, `completed`, `failed`, or `partial`. Allowed decision: `pending`, `continue`, `repeat`, `revise`, or `stop`.

## Handoff rules

1. Preserve the same `run_id` across log, manifest, output filenames, and downstream analysis.
2. Record failed and partial runs; never delete them from the sequence.
3. Treat a missing output as `missing`, not as an empty successful artifact.
4. Do not label a gate passed solely because a script returned exit code zero. Bind the decision to the scientific criterion and an accountable actor.
5. Add later corrections as amendments with old value, new value, reason, actor, and timestamp.
