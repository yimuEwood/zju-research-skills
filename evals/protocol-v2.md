# Evaluation protocol v2

Protocol v2 is a release-control system, not a new benchmark result. The repository currently has no frozen v2 holdout and therefore has no v2 score. Existing v1 aggregates remain available only as legacy internal development evidence; `protocol_v2.py` rejects a v1 manifest instead of migrating or relabelling it.

## Dataset lifecycle

1. Use the published v1 cases only as the development set. Every case in `cases.json`, every pilot case, and every case in a previous run manifest is treated as known.
2. Author a separate case document conforming to `holdout-cases-v2.schema.json`. Gold checks must be structured objects with stable IDs, and each case must be marked `evaluation_status: unseen_holdout`.
3. Compare normalized semantic fingerprints of `prompt + fixture + structured gold criteria`, not only case IDs. Renaming either a case ID or a gold-check ID does not make a known task unseen. NFKC normalization, whitespace normalization, sorted object keys, and criterion-content ordering define the semantic fingerprint; the full frozen file is separately hash-bound for exact auditability.
4. Generate a private cryptographically random secret (at least 256 bits) and nonce (at least 128 bits). Before the first model run, execute `freeze-holdout` with the private secret file. Publish only its SHA-256 commitment in the holdout lock; never publish a deterministic allocation seed.
5. Create exactly one immutable `first-attempt-registry-v2` reservation with `register-attempt` before execution. The lock commits to the fingerprint set and registry genesis. Aggregation rejects attempt numbers other than one, multiple attempt entries, a mismatched run ID, or a registry hash absent from the run manifest.
6. Precommit the two primary rater IDs and optional adjudicator ID in the protocol config before the run manifest is created. Those IDs are part of the protocol-config hash; the aggregator rejects a later substitution, although the host still owns proof of real-world identity and independence.
7. Do not revise Skills, prompts, fixtures, gold criteria, thresholds, or cases after inspecting holdout outputs. A changed case file or content fingerprint invalidates the lock. A failed holdout is not converted into a development result and rerun under the same freeze ID.
8. A release manifest must bind `protocol_version: 2.0`, the frozen split, protocol config, holdout case and lock hashes, first-attempt registry hash, allocation commitment, exact Skill Git commit, the dedicated `rubric-v2.md` hash, rater-schema hash, case IDs, and case fingerprints.

The code can verify hashes and declared exposure history. It cannot prove that a human secretly avoided reading a holdout, that a registry was not backfilled before entering this repository, or that self-declared rater names correspond to independent people. Independent case authorship, controlled access, immutable first-attempt registration, and separately administered execution remain procedural or host-enforced requirements. Configured file paths are confined beneath the configured repository root; private artifacts should therefore live in a protected, ignored subdirectory of that root rather than at an arbitrary external path.

## Balanced blinding

`balanced_arm_map` groups cases by Skill and applies a blocked Latin-square allocation derived from the private secret and nonce. In every complete block of three cases, each arm appears once at A, once at B, and once at C; any remainder has a maximum imbalance of one. The private arm map contains only the public commitment, never the secret, nonce, or a reversible public seed. After all ratings are sealed, `allocation-reveal.json` is executed against its pinned schema, discloses the secret and nonce, proves that they open the commitment, binds the exact rating-file hash bundle, and must have a timestamp strictly later than every rating.

## Tool protocol

The prompt is not evidence of tool isolation. Protocol v2 scans every `events.jsonl` file and rejects the run if it observes:

- a web-search tool item;
- any MCP tool item;
- a shell command that invokes a network client, network-capable Git/GitHub operation, package download, embedded HTTP call, or container pull;
- an empty, missing, malformed, non-UTF-8, or non-object event log entry;
- a log without exactly one trusted completed `turn.completed` or `response.completed` terminal event carrying a stable ID, or a later failed/cancelled/error terminal event;
- an incomplete execution or failed instruction-read audit.

Responses must be strict UTF-8 and contain at least one non-whitespace, non-control character; a byte-nonempty NUL/control-only file is rejected.

This is a deny-list backed audit, not an operating-system network sandbox. Release runs should additionally use a network-disabled execution environment. Any detected deviation is reported with case ID, blind label, event line, and reason and blocks the affected Skill and the overall release.

## Gold checks and rating

Every rater file is executed against `rater-output-v2.schema.json`, not merely read as an informal object. It must declare a path-safe `rater_id`, role, unique `call_id`, model, timestamp, rubric hash, rationale, failure reason, and the SHA-256 of every response it scored. Each expected `check_id` must appear exactly once for every arm. Missing, duplicate, or extra IDs invalidate the rating. A critical failure requires a non-empty reason. The two primary identities must differ, and an adjudicator may not reuse either identity. All run-bundle paths are resolved beneath the run root so a rater ID or symlink cannot redirect evidence lookup outside the bundle.

Two primary raters score the five 0–4 dimensions. The aggregate reports exact and within-one agreement, quadratic weighted kappa by dimension, critical-failure agreement, and binary gold-check agreement. Agreement is measured before adjudication and should be interpreted with its sample size.

## Statistical report and release gate

Quality comparisons are paired by case. The report includes deterministic percentile bootstrap confidence intervals for distilled-minus-baseline differences overall and by Skill. A Skill passes only if all configured requirements hold, including:

- the frozen holdout is valid and contains no known or pilot case;
- every arm has the minimum cases;
- the mean improvement threshold and paired confidence-interval threshold pass;
- the distilled gold-check rate passes;
- the distilled arm has no critical failure;
- no task has a protocol deviation;
- all rating and gold-check adjudications are resolved.
- every response, event log, execution record, and rating file is non-empty and hash-bound; every execution repeats the frozen Skill commit and rubric hash;
- the aggregator recomputes eligibility from the supplied case file, real holdout lock, and first-attempt registry. There is no injectable `dataset_audit` argument.

`status` exposes three distinct booleans: `holdout_ready`, `execution_ready`, and `release_gate_passed`. A frozen holdout alone can never be reported as release-ready. `execution_ready` additionally requires a valid first-attempt reservation. `release_gate_passed` requires eligible current holdout and registry audits, a hash-pinned aggregate, a configured raw run bundle and fixed rater identities. The status path reruns `aggregate_v2` from those raw artifacts and requires the recomputed canonical report to equal the pinned report; a small self-reported summary cannot open the gate. With no validated raw aggregate bundle, the status command exits 2 even if the holdout is ready.

The `aggregate` command likewise exits with 0 only when the complete release gate passes; a complete but non-releasable run exits with 2 so CI cannot mistake successful report generation for successful scientific validation.
