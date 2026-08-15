# L2 deterministic portfolio report

## Result

The protocol-v3 L2 suite contains 400 executable cases: 20 cases for each of the 20 skills. All 400 cases passed the recorded run.

Each skill has:

- 15 positive cases and five explicit negative cases;
- four cases mapped to each of the five capability IDs in the evaluation matrix;
- a unique canonical fixture hash;
- a declared executor path and executor SHA-256;
- an explicit expected outcome;
- two executions whose normalized observations must agree.

The scorer accepts the combined L1 and L2 evidence as valid. All 20 L2 layers are `complete`; L3 and L4 remain `not_run`, so the portfolio remains Beta and the official score remains `null`.

## What this result means

L2 measures reproducible software behavior: parsing, validation, normalization, numeric execution, expected rejection, local artifact production, and provenance checks. It does not measure the factual quality of open-ended scientific prose, literature recall against the live world, or the quality of a research judgment.

Some skills have broad executors; others currently expose only a deterministic contract boundary. A contract-only pass must not be described as end-to-end scientific correctness.

## Reproduce

```powershell
python evals/l2_portfolio_execution.py verify
python evals/l2_portfolio_execution.py run
python evals/score_portfolio_v3.py `
  --results evals/current-portfolio-evidence-v3.json `
  --output evals/results/portfolio-v3-current-score.json
```

The scorer intentionally exits non-zero while L3/L4 are missing even when the input is valid. Inspect `valid_input`, `portfolio_status`, and `official_portfolio_score` in the output rather than interpreting the process exit alone as an L2 failure.

## Release binding

The evidence bundle records a Git commit and exact executor hashes. After any skill executor changes, rerun L2. For a release commit, use a two-commit evidence procedure:

1. commit the code and fixtures;
2. run L2 against that commit;
3. commit the resulting evidence without changing the evaluated executors.

This avoids claiming that an evidence bundle evaluates uncommitted code.

After the final RC code commit, run the following without changing any skill or evaluation code between commands:

```powershell
$rcCommit = (git rev-parse HEAD).Trim()
$shortCommit = $rcCommit.Substring(0, 12)

python evals/generate_l1_census_v3.py `
  --output evals/l1-contract-census-v3.json `
  --results-output evals/current-portfolio-evidence-v3.json `
  --run-id "rc-l1-$shortCommit" `
  --skill-commit $rcCommit

python evals/l2_portfolio_execution.py verify
python evals/l2_portfolio_execution.py run

python evals/score_portfolio_v3.py `
  --results evals/current-portfolio-evidence-v3.json `
  --output evals/results/portfolio-v3-current-score.json
```

Then verify that every record's `skill_commit` equals `$rcCommit`, all executor hashes match files at that commit, `valid_input` is true, all L1/L2 layers are complete, L3/L4 are `not_run`, the status is Beta, and the official score is `null`. Commit only the regenerated evidence in a second evidence commit. The pre-RC `425ea1f...` development bundle is not final release evidence.
