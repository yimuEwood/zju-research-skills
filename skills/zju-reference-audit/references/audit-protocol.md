# Field-level reference audit

## Source hierarchy

Prefer primary metadata from DOI registration records, publisher landing pages, PubMed/Europe PMC, clinical-trial registries, dataset repositories, and patent offices. Use broad indexes for discovery and cross-checking. Record URL and access date for every canonical source.

## Comparison rules

| Field | Material checks |
|---|---|
| DOI/PMID/accession | Normalize prefix and case; exact identifier must resolve to the intended work |
| Title | Ignore punctuation/case; flag additions, omissions, translations, or changed meaning |
| Authors | Compare family names, order, group authors, and truncation policy |
| Year | Distinguish online-first, issue, repository, and DOI registration dates |
| Venue | Normalize accepted abbreviation; detect wrong journal or conference |
| Volume/issue | Compare independently; do not infer one from the other |
| Pages/article number | Preserve e-location versus page range |
| Version/status | Check correction, retraction, expression of concern, preprint, and version of record |

## Report row

Include `audit_id`, `field`, `submitted`, `canonical`, `status`, `severity`, `source_urls`, `retrieved_at`, `confidence`, and `recommended_action`. When sources conflict, list each source value.

## Correction and retraction freshness

Keep bibliographic comparison time separate from publication-status checking. For every correction, retraction, expression-of-concern, or version verdict, record:

- `status_checked_at` in ISO-8601 form;
- authoritative `status_sources` such as a publisher notice, DOI registry relation, PubMed record, or retraction index;
- `version_status` and any linked notice identifier;
- the audit window or `max_age_days` used.

A cached check outside the audit window is `stale`, not negative evidence. If the task affects a manuscript submission, evidence synthesis, clinical claim, or high-stakes decision, refresh the status at the time of audit and retain the retrieval date. Never state “not retracted” from absence in a single secondary index.

## Zotero actions

Recommend granular edits: update DOI; refresh metadata; merge exact duplicates after preserving attachments/notes; link preprint to published version; attach correction or retraction notice; or leave unchanged pending confirmation. Do not claim to have modified Zotero unless the action was actually performed.
