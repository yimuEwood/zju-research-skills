# Alert, scoring, and state rules

## Identity order

1. Normalized DOI.
2. PMID.
3. arXiv ID.
4. OpenAlex ID.
5. Normalized title plus year as a provisional fallback.

Never merge two records only because author surnames and year match. Preserve every source database in `source_databases`.

## Change classes

- `new`: identity has not appeared in prior state.
- `updated`: same identity with a meaningful version, correction, retraction, metadata, or access-state change.
- `duplicate`: no meaningful change.
- `unresolved_identity`: no stable identifier and ambiguous fallback.

## Relevance record

Score dimensions separately from 0 to 2: topic fit, methodological fit, evidence maturity, and actionability. Store a one-sentence reason for every nonzero value. Recalculate the total; do not let a journal name substitute for evidence quality.

## Digest minimum

Include title, year, stable identifier, source database, source level, inclusion reason, relevance dimensions, evidence limit, and lawful access route. Flag corrections, expressions of concern, and retractions prominently.

\n