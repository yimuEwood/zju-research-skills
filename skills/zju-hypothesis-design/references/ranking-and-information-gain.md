# Hypothesis readiness and experiment discrimination

Use this reference when there are several plausible explanations or experiments and the user needs a rational sequence.

## Rank two different things

- **Hypothesis readiness** asks whether an explanation is linked to evidence, specific predictions, falsifiers, boundaries, and feasible tests. It is not the probability that the hypothesis is true.
- **Experiment discrimination** asks how many hypothesis pairs yield different prospective outcome signatures, then adjusts for feasibility, time/cost, and orthogonal measurement. It is not a substitute for power analysis.

Do not mix novelty, prestige, or narrative appeal into either score.

## Prediction signatures

For every experiment, write one prospective signature per compared hypothesis using the same measured variables and time points. Examples: `early_transient_then_recovery`, `monotonic_dose_response`, `no_change_above_measurement_noise`. A pair is discriminated only when both signatures are specified and differ under the prespecified decision rule.

Include `inconclusive_region` so noise, intermediate patterns, and failed manipulations do not get forced into a winner.

Run `python scripts/validate_hypothesis_set.py hypothesis-plan.json`. The report ranks research readiness and discriminatory efficiency, exposes uncovered hypothesis pairs, and rejects v2 experiments that omit prediction signatures.

## Staged portfolio

Select tests in this order unless constraints justify otherwise:

1. calibration/artifact check that could invalidate the premise;
2. low-cost test that separates the largest number of hypothesis pairs;
3. orthogonal measurement that addresses the dominant ambiguity;
4. intervention, rescue, or reversal test for the surviving mechanism;
5. boundary/transfer test after the mechanism is supported in the original system.

The first experiment should maximize decision change per unit cost, not simply have the highest standalone score.

When numeric costs are comparable and the budget is explicit, set one root `cost_unit` and run `python scripts/select_experiment_portfolio.py hypothesis-plan.json --budget N --output experiment-portfolio.json`. Every experiment must state `eligible: true | false`, `feasibility_status: eligible | ineligible | uncertain`, and `ethics_status: approved | not_required | pending | rejected | unknown`. Only the first two ethics states permit selection. Use `depends_on` for prerequisites, `mutually_exclusive_with` for alternatives that cannot coexist, and `required` or root `required_experiment_ids` for non-negotiable tests.

The selector first checks whether required experiments and their transitive dependencies are eligible, mutually compatible, and within budget. It then greedily maximizes newly distinguished hypothesis pairs per incremental dependency-bundle cost, reports ineligible/skipped experiments and uncovered pairs, and uses stable tie breaking. Declared mutual exclusion is treated symmetrically. Treat the output as a transparent constrained scheduling heuristic, not a global optimum; power, sample irreversibility, capacity/time scheduling, stochastic outcomes, and scientific utility beyond pair coverage still require expert review.

## `hypothesis-plan.json` links

Preserve `claim_id`, `gap_id`, `study_id`, and `record_id`. At the plan root, provide `known_evidence_ids` or embed the upstream `evidence_map`; every hypothesis `evidence_ids` and `contradicting_evidence_ids` value must resolve against that universe. Each experiment needs `hypothesis_ids`, `predicted_outcomes`, `decision_rule`, `inconclusive_region`, and the next action for every outcome branch. Pass hypothesis IDs and discriminating keywords to `$zju-literature-monitor` so new evidence can reopen the decision table.
