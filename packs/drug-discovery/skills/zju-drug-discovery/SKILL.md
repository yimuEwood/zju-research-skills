---
name: zju-drug-discovery
description: Assemble and audit target, compound, assay, potency, selectivity, safety, tractability, disease, and translational evidence for early drug-discovery decisions. Use for PubChem, ChEMBL, Open Targets, assay exports, target–compound evidence matrices, hit or lead triage, source-aware candidate ranking, missing-evidence analysis, and experiment prioritization. Do not use heuristic rankings as efficacy, safety, clinical, regulatory, or investment conclusions.
---

# ZJU Drug Discovery

Integrate target and compound evidence without collapsing identity, assay context, uncertainty, and safety into an unexplained score.

## Workflow

1. Establish disease context, target or phenotype strategy, modality, development stage, decision, assay hierarchy, molecule identity, salt/stereochemistry, and hard exclusions.
2. Resolve live identifiers through `$zju-chemistry-databases` and the routes in `references/database-routing.md`. Preserve database record IDs, endpoints, access dates, assay conditions, units, and species.
3. Normalize one candidate record per target–compound pair using `references/candidate-contract.md`. Never pool unlike endpoints or incomparable assay conditions.
4. For an auditable early-stage screen, run:

   `python scripts/prioritize_candidates.py --input candidates.json --output candidate-priority.json`

5. Inspect lane coverage, hard exclusions, score components, source IDs, leave-one-lane-out rank ranges, and unresolved identity or assay conflicts. Use the ranking to choose follow-up evidence, not to declare a winner.
6. Route discriminating experiments through `$zju-hypothesis-design`; register quantitative results with `$zju-statistics-audit`; preserve negative and toxicity findings.

## Boundaries

- Do not compare potency without endpoint, unit, assay format, target construct, species, and uncertainty.
- Do not merge stereoisomers, salts, tautomers, modalities, or targets by name alone.
- Treat safety flags and failed experiments as first-class evidence.
- Require medicinal chemistry, DMPK, toxicology, clinical, and regulatory experts for decisions in their domains.
- Do not expose restricted structures or confidential programs to public services.

## Output

Return a source-linked candidate matrix, deterministic heuristic ranking, sensitivity range, exclusions, missing-evidence plan, and the next discriminating experiment. Label the ranking `decision_support_not_validation`.
