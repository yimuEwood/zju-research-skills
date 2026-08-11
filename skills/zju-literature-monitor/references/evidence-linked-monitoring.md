# Evidence-linked monitoring

Use this reference after an evidence synthesis or hypothesis plan exists. The monitor should watch decisions, not just keywords.

## Watch-target types

Maintain stable watch targets in the profile:

- `query`: broad and domain-specific saved searches;
- `seed_record`: forward citations, related records, and version transitions;
- `claim`: new support, contradiction, correction, or retraction for a synthesis claim;
- `gap`: a missing population/system, method, outcome, time point, mechanism, or negative result;
- `hypothesis`: evidence whose prospective pattern would change a decision rule;
- `trial_or_protocol`: status/result changes for registered work;
- `dataset_or_code`: releases that make a prior result reproducible or re-analyzable.

Each target stores `target_id`, `target_type`, `query_or_identifier`, `decision_changed_if`, cadence, and last checked time.

## Run portfolio

A complete update cycle should include, when supported:

1. date-bounded rerun of the frozen core queries;
2. forward-citation and related-record expansion for seed records;
3. version/publication/correction/retraction check for preprints and pivotal studies;
4. gap-targeted queries from the current `evidence-map.json`;
5. prediction-signature queries from `hypothesis-plan.json`;
6. one contradiction-oriented query for null, adverse, failure, or non-replication evidence.

Do not compare raw result counts across query versions as trend evidence. Compare the same source, query version, and coverage window.

## Decision-changing digest

Prioritize records by downstream effect:

1. correction, expression of concern, or retraction affecting an active claim;
2. result matching a prespecified hypothesis discriminator;
3. direct evidence that closes or reframes an important gap;
4. stronger/independent replication or substantive contradiction;
5. method, dataset, or code enabling reanalysis;
6. general topic relevance.

Pass record IDs to `$zju-fulltext-access`, then paper spines to `$zju-evidence-synthesis`. Reopen certainty or hypothesis status only after those stages, not from a title or abstract alert.
