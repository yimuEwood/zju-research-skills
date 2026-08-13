# Twenty-skill evaluation protocol v3

Protocol v3 evaluates all twenty ZJU research skills. It is deliberately stricter than the earlier reports: the first seven skills had a three-arm internal comparison, twelve expansion skills had post-remediation single-arm development checks, and the Research Director had routing and engineering tests. Those results remain useful development history, but they are not one comparable twenty-skill capability score.

## Four evidence layers

| Layer | Weight | Minimum per skill | What it measures | 95% uncertainty |
|---|---:|---:|---|---|
| L1 contract conformance | 5% | six required census checks | installability, trigger/output contracts, resource links, provenance, evaluation mapping | none; this is a release census, not a statistical sample |
| L2 deterministic function | 20% | 20 independent cases, including at least five negative/boundary cases | parsers, validators, transformations, artifact creation, expected rejection | Wilson binomial interval |
| L3 controlled task capability | 40% | 12 cases, at least three in each of four strata | blinded three-arm performance on known development tasks | paired case bootstrap |
| L4 frozen holdout generalization | 35% | 15 first-attempt cases, at least three in each of five strata | blinded performance on untouched tasks under independent administration | paired case bootstrap |

The minimum complete run is therefore 53 case/check units per skill before arm replication: 1,060 units across the portfolio. L3 and L4 use three arms, producing at least 2,140 arm-level records in a complete run. Every capability in `skill-evaluation-matrix-v3.json` must be exercised by L3 and L4 cases; deterministic capabilities must also be represented in L2.

## Comparison and scoring

The three arms are `no_skill`, the strongest license-compatible open-source baseline selected and pinned for that skill, and the distilled ZJU skill. Case allocation is blocked within skill. Holdout allocation additionally uses a committed secret. The primary comparison is paired by case against the stronger of the two controls:

`gain = distilled - max(no_skill, strongest_open_source_baseline)`

Two separately invoked primary raters score each task arm. Their IDs, response hashes, scores, and critical-failure decisions are retained. A score gap greater than ten points or a critical-failure disagreement requires adjudication. Rater IDs document separate calls but are not by themselves proof of human or institutional independence.

Each layer is reported separately with `n`. The official composite, when eligible, is:

`0.05 * L1 + 0.20 * L2 + 0.40 * L3 + 0.35 * L4`

The composite 95% interval is computed by case-level bootstrap within skill and layer. L1 is fixed because it is a census. L2, L3, and L4 are resampled independently; arm comparisons remain paired within a task case.

## Stable gate

A skill remains Beta unless all four layers are complete. Stable additionally requires, among other gates:

- L1 equals 100% and L2 pass rate is at least 90%, with a Wilson lower bound of at least 75%;
- L3 distilled mean is at least 75, gold-check rate is at least 80%, and mean gain over the strongest paired control is at least five points;
- L4 distilled mean is at least 80, gold-check rate is at least 85%, mean gain is at least five points, and the paired gain interval is strictly positive;
- composite score is at least 80 and its lower confidence bound is at least 75;
- no distilled critical failure, unresolved adjudication, protocol deviation, known holdout case, post-exposure revision, or missing first-attempt/independent-administration evidence exists.

The portfolio becomes Stable only if all twenty skills independently pass. One high score cannot compensate for an untested or failing skill. L3/L4 release evidence also needs a signed verification bundle from a verifier key registered before the holdout freeze; the secret key remains with the independent administrator and is never committed to the repository.

## Claim boundary

L1 or validator results may be described as engineering readiness, never as scientific capability or an `A/100` quality score. L3 is a development benchmark and must be labelled as such. If L4 is absent, the scorer withholds both the official per-skill composite and the portfolio score. Failed cases remain visible and should drive another distillation cycle; they must not be repaired and silently reused as holdout evidence.

Run the current evidence declaration with:

`python evals/score_portfolio_v3.py --results evals/current-portfolio-evidence-v3.json --output portfolio-score-v3.json`

Until a separately authored frozen holdout is executed, the correct result is `Beta`, `official_score: null`, and `holdout: not_run`.
