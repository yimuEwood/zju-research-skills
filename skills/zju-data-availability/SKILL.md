---
name: zju-data-availability
description: Inventory, plan, draft, or audit manuscript data and code availability with dataset-level traceability, FAIR metadata, repository and identifier routing, licensing, controlled-access conditions, and claim-to-file mapping. Use for journal data statements, source data, code sharing, accession planning, sensitive human data, third-party restrictions, or Chinese-to-English availability wording.
---

# ZJU Data Availability

## Mandatory Inventory Semantics

Preserve every supplied embargo, access, artifact, and location fact. Always emit one inventory row per known or implied artifact with stable ID, claim/figure link, current status, exact location, repository/identifier, restriction, owner, and unresolved fields. If only broad locations are supplied, create distinct rows with `DETAILS_NOT_SUPPLIED` rather than collapsing them into prose. Embargo statements must separately state `current_access`, release trigger/date, and repository/deposit plan; never imply current access.

Map every claim-supporting artifact to an honest access route before writing a polished statement.

## Intake Gate

Confirm journal/article type and obtain current journal instructions when venue-specific wording matters. Inventory data, code, software environments, materials, models, protocols, source images, supplementary files, and third-party inputs. If an artifact does not exist or cannot be shared, state that directly and determine the basis.

Read `references/data-routing.md` for access routes and `references/statement-contract.md` before drafting.

## Workflow

1. Assign stable IDs to every claim-supporting dataset/code/material artifact and link them to manuscript sections, figures, tables, claims, analysis IDs, and result IDs.
2. Record controller/owner, sensitivity, consent/ethics constraints, third-party terms, file formats, size, software needs, checksums, and current location. Do not copy credentials or restricted data into the inventory.
3. Choose one primary route for each artifact: public repository, discipline repository, controlled access, within article/supplement, reused public source, third-party restricted, justified request, or not applicable.
4. Select repository, persistent identifier, version, license, metadata, and embargo strategy before drafting. Verify current ZJU and journal options from authoritative pages; do not invent a repository or accession.
5. Prepare files with nonproprietary formats where feasible, documentation, data dictionary, code environment, provenance, and checksums. Separate raw, processed, QC, analysis, result-registry, figure-source, table-source, and final presentation artifacts while recording `derived_from` lineage.
6. For sensitive data, describe eligibility, request procedure, decision authority, review criteria, expected response time, and legal/ethical limits without promising access that cannot be granted.
7. Build one reproducibility package per computational result family: entrypoint, environment, ordered input artifact IDs, expected output/result IDs, random seeds when relevant, and a verification record. Do not equate file presence with a reproduced result.
8. Draft a dataset-to-location statement and formal dataset/code citations. Treat “available upon reasonable request” as unresolved unless a specific justified mechanism is supplied.
9. Run `scripts/validate_data_inventory.py` and `$zju-statistics-audit/scripts/validate_result_handoff.py`; reconcile every `supports_claims`, `supports_results`, reproducibility-package output, and statement with the canonical claim/result registries, methods, figure/table source files, repository records, supplementary files, and author contributions.

## Red Lines

- Never invent DOI/accession numbers, licenses, ethics approvals, committees, embargo dates, or access conditions.
- Do not expose personal, controlled, export-restricted, proprietary, or credential-bearing content.
- Do not call data FAIR merely because it is downloadable.
- Do not claim reproducibility when code, environment, random seeds, or essential inputs are missing.

## Output Contract

Return:

1. `Artifact inventory and claim/result map` with derivation lineage.
2. `Access-route and repository plan`.
3. `FAIR/metadata and reproducibility packages/gaps`, including result coverage.
4. Ready-to-paste data/code availability statement.
5. Dataset/code citation list.
6. `Unresolved identifiers, restrictions, and owner decisions`.
