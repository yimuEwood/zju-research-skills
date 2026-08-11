# Research-to-invention map

Use this contract to transform `$zju-paper-reader` Paper Cards and `$zju-experiment-log` records into an attorney-facing technical map. It organizes technical evidence; it does not decide novelty, inventive step, enablement, ownership, or infringement.

## Cross-skill mapping

| Research artifact | Invention-map field | Rule |
|---|---|---|
| Research question or documented limitation | `technical_concepts[].problem` | State the concrete technical limitation and operating context, not a market slogan. |
| Method, apparatus, composition, code path, or process step | `features[]` | Split into atomic features; retain exact source IDs, anchors, parameters, and support state. |
| Result-registry row or anchored paper result | `technical_concepts[].technical_effect` and `evidence_ids` | Keep conditions, magnitude, uncertainty, comparator, and result ID. Do not generalize beyond them. |
| Failed run, boundary condition, or negative result | `discriminating_evidence_ids` or supported boundary | Use it to distinguish embodiments or define a tested boundary; do not convert one failure into a universal exclusion. |
| Alternative material, component, sequence, or parameter regime | `alternative_embodiments[]` | Include only source-supported alternatives; state what is replaced, what effect is retained, and what evidence distinguishes it. |
| Paper limitation or unresolved mechanism | inventor question | Do not silently fill the gap with an embodiment or effect. |

## Four linked maps

### Problem-solution-effect

Each `technical_concept` needs `concept_id`, `problem`, `solution_feature_ids`, `technical_effect`, `evidence_ids`, and `discriminating_evidence_ids`. Keep effect wording bounded to tested conditions.

### Feature-evidence

Use the feature ledger in `source-to-claim.md`. One feature may cite multiple sources, but each formal-claim feature needs exact anchors. Preserve result IDs in `evidence_ids` or `result_ids` when experiment outputs are the support.

### Claim dependency

Represent a drafting outline, not final legal language:

```yaml
claim_candidates:
  - claim_id: CL-1
    claim_role: independent
    parent_claim_ids: []
    concept_ids: [IC-1]
    feature_ids: [FT-1, FT-2]
  - claim_id: CL-2
    claim_role: dependent
    parent_claim_ids: [CL-1]
    concept_ids: [IC-1]
    feature_ids: [FT-3]
```

A dependent candidate must add at least one supported feature and point to an existing parent. The dependency graph must be acyclic.

### Prior-art query map

For each concept provide `problem_terms`, `solution_terms`, `effect_terms`, `synonyms`, optional `classification_hints`, `date_cutoff`, and target databases. `validate_feature_ledger.py` deterministically emits Boolean query blocks and the feature/concept IDs they test. A generated query is a search plan, not proof of novelty.

## Alternative embodiments and discrimination

For each embodiment record:

```yaml
embodiment_id: EB-1
concept_id: IC-1
replaces_feature_ids: [FT-2]
alternative_feature_ids: [FT-3]
target_effect: ""
support_state: explicit | inherent | needs_confirmation | unsupported
evidence_ids: [X-RESULT-2]
discriminating_evidence_ids: [X-RESULT-3]
```

`Discriminating evidence` is evidence capable of separating the alternative from the baseline under stated conditions. If it is absent, keep the embodiment as a confirmation question and outside formal claim candidates.

Set `workflow_version: "2.0"` and include `technical_concepts`, `claim_candidates`, `prior_art_terms`, and `alternative_embodiments` beside the source and feature ledgers. The validator returns all four maps, dependency order, query blocks, evidence gaps, and `invention_map_ready`.
