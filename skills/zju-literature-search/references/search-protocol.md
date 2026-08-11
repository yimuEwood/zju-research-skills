# Reproducible search protocol

## Question frame

Record the following before searching:

| Field | Required content |
|---|---|
| Objective | One answerable sentence |
| System/population | Material, organism, cohort, device, or process |
| Intervention/exposure | Treatment, property, method, or condition |
| Comparator | Control, baseline, competing method, or `not applicable` |
| Outcomes | Primary and secondary outcomes with units when relevant |
| Eligible designs | Experimental, observational, computational, review, patent, etc. |
| Limits | Date, language, species, geography, document type |
| Exclusions | Explicit reasons that can be applied consistently |

## Query construction

Build one synonym block per concept. Join synonyms with `OR`, then join concepts with `AND`. Preserve abbreviations and spelling variants. Add controlled vocabulary when the source supports it, but retain free text for new terminology.

- Biomedicine: `(MeSH OR free text)` blocks for population, intervention/exposure, and outcome. Apply study-design filters only when their recall loss is acceptable.
- Chemistry: entity names, CAS or registry identifiers when known, structure class, reaction/transformation, property, and application.
- Materials: composition, phase/structure, synthesis/process, measured property, and device/application.
- Agriculture: scientific and common organism names, cultivar/breed where relevant, production system, stressor/intervention, outcome, and region/climate.

## Search log

For every source record: `query_id`, database/platform, exact query, filters, executed timestamp with timezone, result count, export format, and notes. Never substitute a reconstructed query for the executed query without marking it reconstructed.

## Evidence table schema

Required fields:

`record_id`, `title`, `authors`, `year`, `venue`, `document_type`, `abstract`, `doi`, `pmid`, `other_identifier`, `landing_url`, `source_database`, `query_ids`, `retrieved_at`, `screening_stage`, `decision`, `exclusion_reason`, `verification_status`.

Recommended extraction fields:

`study_system`, `design`, `sample_size`, `intervention_or_exposure`, `comparator`, `outcomes`, `effect_direction`, `effect_size`, `uncertainty`, `limitations`, `notes`.

## Screening flow

1. Remove exact identifier duplicates.
2. Merge probable title/year duplicates while preserving all source and query IDs.
3. Screen title/abstract against inclusion criteria.
4. Retrieve eligible full text lawfully.
5. Record one explicit reason for each full-text exclusion.
6. Reconcile uncertain decisions rather than silently guessing.
