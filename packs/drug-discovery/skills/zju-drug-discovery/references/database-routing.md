# Database routing

| Need | Public route | Required provenance |
|---|---|---|
| Compound identity and calculated properties | PubChem PUG REST | CID, query representation, endpoint, access time |
| Bioactivity and assay context | ChEMBL API | molecule, target, assay and activity IDs; units and relations |
| Target–disease association | Open Targets Platform | target and disease IDs, evidence datasource and score |
| Structures and vendors | licensed or institutional source when authorized | exact structure, access terms, retrieval record |
| Clinical development | ClinicalTrials.gov and regulatory sources | trial ID, version/date, intervention and status |
| Literature support | `$zju-literature-search` and `$zju-reference-audit` | query log, DOI/PMID, source anchor |

Use live API clients only in read-only mode with bounded pagination, caching, and documented terms. A database hit is not independent biological replication.
