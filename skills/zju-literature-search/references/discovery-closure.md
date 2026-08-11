# Discovery closure and search map

Use this reference when the task needs more than a single keyword query: cross-disciplinary questions, seed expansion, evidence-gap searches, or a defensible stopping decision.

## Decompose the question into retrieval views

Build a concept ledger before writing source syntax. Give every concept a stable `concept_id` and record synonyms, controlled vocabulary, identifiers, exclusions, and the field that contributed the term.

Create at least these query views when applicable:

1. **Phenomenon view**: system/population + intervention/exposure + outcome/property.
2. **Mechanism view**: proposed mechanism + mediators/pathways + measurement method.
3. **Method view**: assay, instrument, model, synthesis route, or computational method.
4. **Translation view**: application, device, clinical/agricultural setting, scale-up, or failure mode.
5. **Contradiction view**: negative, null, failure, instability, adverse, non-replication, correction, or retraction terms.

Do not force every field into one over-constrained query. Run orthogonal views and connect them through `concept_id`, `query_id`, and `record_id`.

## Seed and citation expansion

After the first eligible set, choose seeds for distinct reasons: foundational mechanism, strongest recent evidence, contradictory result, enabling method, and closest target system. For each seed record:

- chase backward references for origins and methods;
- chase forward citations for replications, boundary conditions, and corrections;
- request related-record neighborhoods from a citation-graph source;
- search distinctive phrases, named assays/materials, registry IDs, and author keywords;
- record every edge as `from_record_id`, `to_record_id`, `relation`, `source`, and `retrieved_at`.

Citation count is a discovery aid, never an inclusion or quality criterion.

## Saturation and stopping

Stopping is a protocol decision, not “the results look sufficient.” Track unique and eligible IDs per round. Run:

`python scripts/assess_search_saturation.py search-rounds.json --output saturation.json`

Default stop recommendation requires all declared concept and source-family coverage, at least one citation/seed-expansion round, no open critical evidence gap, and two consecutive rounds with at most one new eligible record. Change thresholds prospectively when the expected evidence base is unusually small or large. Report both the rule and the observed marginal yield.

## `search-map.json` handoff

Preserve this minimum structure:

```yaml
question_id: Q-001
question_frame: {}
concepts: [{concept_id: C01, label: "", synonyms: [], exclusions: []}]
queries: [{query_id: QRY-01, source: "", exact_query: "", executed_at: "", record_ids: []}]
seed_graph: [{from_record_id: REC-1, to_record_id: REC-2, relation: forward_citation}]
records: []
screening: {included_ids: [], excluded: [], review_needed_ids: []}
saturation_artifact: saturation.json
evidence_gaps: [{gap_id: GAP-01, description: "", severity: important, status: open}]
next_handoff:
  fulltext_record_ids: []
  synthesis_question_ids: [Q-001]
  monitor_seed_ids: []
```

The next skill must reuse these IDs rather than renaming studies or reconstructing the search history.
