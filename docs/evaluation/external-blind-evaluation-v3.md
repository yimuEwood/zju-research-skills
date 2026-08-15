# External L3/L4 blind evaluation handbook

L3 and L4 cannot be completed honestly by the project authors alone. This handbook defines the handoff to independent people and the artifacts they must return. An internal rehearsal may test the machinery, but it is never entered as independent release evidence.

## Required people

Use named people with documented conflicts of interest. One person may not hold two roles.

| Role | Minimum | Independence requirement | Sees arm identity? |
|---|---:|---|---|
| Evaluation administrator | 1 | Not an author or evaluator of the distilled skills | Only after ratings are frozen |
| Primary raters | 2 | Separate rating calls; no collaboration on scores | No |
| Adjudicator | 1 | Different from both primary raters | No, until adjudication is closed |
| Holdout case authors | 2 or more | Cases not disclosed to skill authors or model operators | No need |

For L4, the administrator owns the unseen cases, allocation secret, first-attempt registry, and Ed25519 private key. The private key and allocation secret must stay outside the repository.

## Minimum task inventory

- L3: 12 cases per skill, 240 cases total, across nominal, incomplete-input, edge-case, and cross-skill-handoff strata.
- L4: 15 cases per skill, 300 cases total, across nominal, incomplete-input, adversarial, cross-domain, and artifact-or-handoff strata.
- Each capability ID must occur at least twice in each layer.
- L4 fingerprints must not overlap L2, L3, pilot cases, repository fixtures, or known public benchmark prompts.

Every case contains a prompt, immutable fixture, observable gold checks, capability IDs, and a stratum. Case renaming does not evade duplicate detection because the fingerprint omits `case_id`. Keep fixture files as bytes, not only as a path or filename; the artifact manifest hashes those bytes and the scorer checks that binding.

## Before any model call

1. Case authors give the task document directly to the administrator.
2. The administrator audits it with `audit-tasks` and a known-fingerprint registry.
3. The project and administrator choose the strongest eligible baseline for every skill before seeing responses. Each baseline packet is commit-pinned and hashed.
4. The administrator creates an Ed25519 key outside the repository. Only the public key is preregistered in `evals/trusted-verifier-keys-v3.json`, and that registration is committed before the L4 freeze.
5. The administrator creates an allocation secret of at least 32 characters, stores it outside the repository, and freezes only its SHA-256 commitment.
6. The L4 task document, fingerprints, allocation commitment, and registry genesis are frozen.
7. Attempt 1 is registered after freeze but before the first model call.

Example commands:

```powershell
# Run on the independent administrator's machine. Do not place these secrets in the repository.
python evals/blind_eval_operations_v3.py keygen `
  --private-key D:\independent-eval\admin-private.pem `
  --key-id external-admin-2026-01 `
  --registration-output D:\independent-eval\public-registration.json

python evals/blind_eval_operations_v3.py freeze-holdout holdout-tasks.json `
  --known all-known-fingerprints.json `
  --freeze-id zju-v3-holdout-001 `
  --frozen-at 2026-09-01T09:00:00+08:00 `
  --secret-file D:\independent-eval\allocation-secret.txt `
  --nonce 2026-independent-nonce-01 `
  --output holdout-lock.json

python evals/blind_eval_operations_v3.py register-first-attempt holdout-lock.json `
  --run-id zju-v3-independent-001 `
  --registered-at 2026-09-01T09:05:00+08:00 `
  --execution-started-at 2026-09-01T09:10:00+08:00 `
  --output first-attempt-registry.json

python evals/blind_eval_operations_v3.py baseline-selection baseline-choices.json `
  --selected-at 2026-09-01T08:45:00+08:00 `
  --output baseline-selection.json

python evals/blind_eval_operations_v3.py rater-precommit `
  --primary-raters rater-01 rater-02 `
  --adjudicator adjudicator-01 `
  --rubric evals/rubric-v3.md `
  --output rater-precommit.json

python evals/blind_eval_operations_v3.py run-manifest `
  --run-id zju-v3-independent-001 `
  --skill-commit 0000000000000000000000000000000000000000 `
  --baseline-selection baseline-selection.json `
  --rater-precommit rater-precommit.json `
  --holdout-lock holdout-lock.json `
  --first-attempt first-attempt-registry.json `
  --execution-started-at 2026-09-01T09:10:00+08:00 `
  --output run-manifest.json
```

Replace the zero commit with the exact evaluated commit. `baseline-choices.json` must cover all twenty skills; the one-row template is only a shape example.

Generate and keep separate private allocations for the L3 and L4 task documents. Do this after the relevant task document is final and before model calls:

```powershell
python evals/blind_eval_operations_v3.py allocate l3-tasks.json `
  --freeze-id zju-v3-l3-001 `
  --secret-file D:\independent-eval\allocation-secret-L3.txt `
  --nonce 2026-independent-L3-nonce `
  --output private-allocation-L3.json

python evals/blind_eval_operations_v3.py allocate holdout-tasks.json `
  --freeze-id zju-v3-holdout-001 `
  --secret-file D:\independent-eval\allocation-secret-L4.txt `
  --nonce 2026-independent-L4-nonce `
  --output private-allocation-L4.json
```

Do not give either private allocation file to raters or project authors. Later, build separate response, artifact, execution-event, ratings-lock, and reveal files for L3 and L4. When both layers are assembled, pass the two paths after each plural command option in layer order.

## Execute the three arms

Use the same host, model family and version, context limit, temperature policy, time budget, network policy, database entitlements, and tool budget for all arms. The only permitted difference is the instruction packet:

- `no_skill`: no research skill instructions;
- `strongest_open_source_baseline`: the preselected pinned baseline;
- `distilled_skill`: the pinned ZJU skill package.

Allocation is blocked within skill using a committed secret Latin square. Keep the arm map private. L3 and L4 use separate commitments and separate reveals; every assignment key includes `layer`, so a repeated `case_id` cannot silently cross layers. Record a unique model-call ID, response path, response hash, event/trace hash, model identifier, and tool policy for every arm. Identical response hashes across arms fail closed because they cannot demonstrate independent calls.

Build the response, fixture/artifact, and execution-event manifests while their files are still available to the administrator:

```powershell
python evals/blind_eval_operations_v3.py responses response-rows.json --output response-manifest.json
python evals/blind_eval_operations_v3.py artifacts artifact-rows.json --output artifact-manifest.json
python evals/blind_eval_operations_v3.py execution-events execution-events.json --output execution-event-manifest.json
```

The templates under `evals/templates/v3/` show the required `run_id`, `layer`, call IDs, and file paths. Prefer paths relative to the evidence-package root and import those files with the signed bundle; absolute paths from the administrator's machine are not portable release evidence. The manifest builder reads each fixture and artifact and records its byte size and SHA-256. If a file changes before final assembly—or is missing or different when the repository scorer imports the package—the run fails.

## Double-blind rating

After all responses are complete, build one packet for each primary rater. The packet contains blind labels A/B/C, response hashes, prompts, fixtures, and gold checks. It contains neither arm names nor skill identities.

```powershell
python evals/blind_eval_operations_v3.py rater-packet holdout-tasks.json response-manifest.json `
  --rater-id rater-01 --rubric evals/rubric-v3.md --output rater-01-packet.json
```

Each response receives exactly two primary ratings with unique rating-call IDs. A score gap over ten points, a critical-failure disagreement, or a gold-check disagreement is adjudicated by the precommitted third person. Raters must cite observable response locations rather than infer which system produced the response.

## Reveal and aggregate

Do not reveal until every rating and required adjudication is immutable and hashed. Create one ratings lock per layer. Its hash covers the exact two primary rating objects and any adjudication object, keyed by layer, skill, case, blind label, and response hash.

```powershell
python evals/blind_eval_operations_v3.py ratings-lock portfolio-results.json `
  --layer L3_controlled_task_capability `
  --sealed-at 2026-09-03T18:00:00+08:00 `
  --response-manifest response-manifest-L3.json `
  --output ratings-lock-L3.json

python evals/blind_eval_operations_v3.py reveal l3-tasks.json `
  --freeze-id zju-v3-l3-001 `
  --secret-file D:\independent-eval\allocation-secret-L3.txt `
  --nonce 2026-independent-L3-nonce `
  --ratings-lock ratings-lock-L3.json `
  --ratings-completed-at 2026-09-03T18:00:00+08:00 `
  --revealed-at 2026-09-03T18:05:00+08:00 `
  --output allocation-reveal-L3.json

python evals/blind_eval_operations_v3.py ratings-lock portfolio-results.json `
  --layer L4_frozen_holdout_generalization `
  --sealed-at 2026-09-03T18:00:00+08:00 `
  --response-manifest response-manifest.json `
  --output ratings-lock-L4.json

python evals/blind_eval_operations_v3.py reveal holdout-tasks.json `
  --freeze-id zju-v3-holdout-001 `
  --secret-file D:\independent-eval\allocation-secret.txt `
  --nonce 2026-independent-nonce-01 `
  --ratings-lock ratings-lock-L4.json `
  --ratings-completed-at 2026-09-03T18:00:00+08:00 `
  --revealed-at 2026-09-03T18:05:00+08:00 `
  --output allocation-reveal.json

python evals/blind_eval_operations_v3.py aggregate ratings.json allocation-reveal.json `
  --iterations 10000 --output paired-bootstrap-summary.json
```

The primary comparison is paired by case:

`distilled_skill - max(no_skill, strongest_open_source_baseline)`

The aggregate tool reports a paired case bootstrap interval. It deliberately writes `independent_result: false`; only a valid signed verification bundle accepted by the repository scorer can establish independent evidence.

## Administrator signature and project import

The administrator assembles the run manifest, holdout lock, first-attempt registry, layer-specific reveals, response manifests, fixture/artifact manifests, ratings locks, execution-event manifests, exact portfolio results, and administration attestation. The assembler re-hashes local files, matches every response and artifact to a result record, checks the pre-reveal ratings lock, and writes the exact canonical portfolio-results hash into the bundle.

Run `assemble-verification` with one path per executed layer (two paths when both L3 and L4 are present). Before assembly, compute the evidence-component hash and copy it into the attestation's `evidence_bundle_sha256`; the assembler refuses a mismatch.

```powershell
python evals/blind_eval_operations_v3.py evidence-components `
  --run-manifest run-manifest.json `
  --allocation-reveals allocation-reveal-L4.json `
  --response-manifests response-manifest-L4.json `
  --artifact-manifests artifact-manifest-L4.json `
  --ratings-locks ratings-lock-L4.json `
  --execution-event-manifests execution-event-manifest-L4.json `
  --output evidence-components.json

python evals/blind_eval_operations_v3.py assemble-verification `
  --verification-id external-zju-v3-001 `
  --verified-at 2026-09-03T18:10:00+08:00 `
  --verifier-id external-admin-01 `
  --verification-key-id external-admin-2026-01 `
  --run-manifest run-manifest.json `
  --holdout-lock holdout-lock.json `
  --first-attempt first-attempt-registry.json `
  --allocation-reveals allocation-reveal-L4.json `
  --response-manifests response-manifest-L4.json `
  --artifact-manifests artifact-manifest-L4.json `
  --ratings-locks ratings-lock-L4.json `
  --execution-event-manifests execution-event-manifest-L4.json `
  --portfolio-results portfolio-results.json `
  --attestation administration-attestation.json `
  --output unsigned-verification.json
```

Then sign the assembled canonical bundle with the preregistered Ed25519 private key:

```powershell
python evals/blind_eval_operations_v3.py sign unsigned-verification.json `
  --private-key D:\independent-eval\admin-private.pem `
  --output signed-verification.json

python evals/score_portfolio_v3.py `
  --results portfolio-results.json `
  --verification signed-verification.json `
  --output scored-portfolio.json
```

The project imports only the signed public evidence package. It must not receive the private key before or after evaluation. The bundle records both a canonical object hash and the SHA-256 plus byte length of the exact `portfolio-results.json` file passed to assembly. The repository scorer reads that same file, verifies its bytes and signature again, recomputes the object hash, rechecks manifest self-hashes, and rebinds ratings, fixture bytes, generated artifacts, response calls, and layer-aware assignments. Reformatting or replacing the results file after signing invalidates the run.

The scorer exits `0` only when every release gate is satisfied, `2` for a valid but incomplete/Beta portfolio, and `1` for invalid evidence. With no independently signed L3/L4 package, the current repository evidence remains Beta, its official portfolio score remains `null`, and the command exits `2`.

## Conditions that invalidate a run

- case or gold-check edits after freeze;
- a known, pilot, repository, or development fingerprint in L4;
- baseline selection after any response is viewed;
- rerunning a failed L4 case and presenting the rerun as first attempt;
- different tool or entitlement budgets across arms;
- repeated response hashes or call IDs;
- ratings performed with arm identity visible;
- a ratings lock created after reveal, or a reveal that points to a different ratings lock;
- allocation revealed before ratings close;
- an administrator key registered after freeze;
- a signature made by a project author or an unregistered key;
- missing raw response, trace, fixture, or rating hashes.
- a single ambiguous reveal reused across L3 and L4.

Failed first-attempt cases remain failed. They may move into development after reveal, but another release requires new unseen holdout cases.
