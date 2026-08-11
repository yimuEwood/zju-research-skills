# Source-package assembly

Use this reference when the downstream task is close reading or evidence synthesis rather than merely opening one PDF.

## Resolve a source family

Treat these as related but non-interchangeable artifacts: preprint versions, accepted manuscript, version of record, correction or retraction notices, supplementary methods/figures/tables/data, protocol/registration, and linked data/code. Match artifacts by stable identifiers, explicit version links, title/authors, and publisher/repository metadata. Do not silently replace the requested version with an easier-to-access version.

## Acquisition queue

Prioritize components by the downstream question:

1. Obtain the exact version needed for the claim or citation.
2. Obtain correction/retraction notices before interpreting results.
3. Obtain supplements containing methods, exclusions, robustness checks, or outcomes used downstream.
4. Obtain protocol/registration when prespecification matters.
5. Obtain linked data/code for reproduction or reanalysis.

If a component is unavailable, keep it in `missing_components`; do not treat the package as complete.

## `source-pack.json` handoff

```yaml
record_id: REC-00001
target_identifiers: {doi: "", pmid: "", other: ""}
version_requested: version_of_record
version_obtained: ""
primary_source: {url: "", local_path: "", source_level: full_text}
notices: [{type: correction, identifier: "", url: ""}]
supplements: [{component_id: SUP-01, type: methods, url: "", local_path: ""}]
related_assets: {protocol: [], registration: [], data: [], code: []}
missing_components: []
access_log: []
reader_handoff: {record_id: REC-00001, ready: false, blocker: ""}
```

Reuse the upstream `record_id`. The reader must report exactly which package components it inspected.
