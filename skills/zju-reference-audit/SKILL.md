---
name: zju-reference-audit
description: Verify scholarly reference metadata field by field and assess whether in-text citations support nearby claims. Use for DOI, PMID, title, author, year, venue, volume, issue, page, retraction, duplicate, citation-placement, and Zotero correction audits. Do not fabricate missing metadata or treat bibliographic existence as proof that a source supports a claim.
---

# ZJU Reference Audit

Separate two questions: “Is this reference bibliographically correct?” and “Does it support this exact claim?” A reference can pass one and fail the other.

For every audit response, use these four headings even for a short question: `Bibliographic identity`, `Claim support`, `Evidence anchors`, and `Safe correction or wording`. Do not return an unstructured prose-only verdict.

## Workflow

1. Preserve the submitted reference and assign a stable audit ID. Parse identifiers and fields without silently repairing the source text.
2. Resolve the work through at least two independent authoritative metadata sources when practical: DOI registration agency, PubMed/Europe PMC, publisher record, Crossref, OpenAlex, or another domain registry. Record source URLs and retrieval dates.
   Resolve a DOI or PMID through the collection's bounded provider executor with:

   `python scripts/resolve_reference.py --doi 10.x/example --snapshot-dir provider-snapshots --output resolution.json`

   The resolver accepts recorded snapshots by default and uses the same explicit live opt-in, allowlist, page/size limits, and HTTP-200 payload validation as `$zju-literature-search`. It retains every provider value, labels source conflicts instead of choosing silently, and reports `verified_two_source` only after at least two exact-identifier matches without material conflicts. Read `references/reference-resolution.schema.json` before using the canonical row.
3. Compare submitted and canonical fields using `references/audit-protocol.md`. For structured local metadata, run:

   `python scripts/compare_metadata.py --input audit-records.json --output differences.json`

4. Check duplicate records, corrections, retractions, expressions of concern, and version mismatches. Record the status sources and `status_checked_at`; a cached status older than the declared audit window is evidence of a prior check, not a current verdict. Validate local status records when JSON is available:

   `python scripts/validate_status_freshness.py --input status-records.json --as-of 2026-08-10 --max-age-days 90`
5. For each cited claim, inspect the source passage or data when available and apply `references/support-assessment.md`. Never infer support from title or abstract alone when the claim depends on methods, subgroup results, magnitude, or causality. For a structured claim/fact package, run `python scripts/audit_claim_support.py claim-support.json`; a material claim atom passes only when its key, value, unit, source ID, and non-placeholder anchor resolve to matching evidence.
6. Return field-level differences, evidence URLs, confidence, recommended corrected citation, and Zotero actions. Keep unresolved conflicts explicit.

Even when only a claim and study-design description are supplied, keep the two audit lanes visible. Report `Bibliographic identity: not assessed` unless a reference record is present, and separately assess `Claim support`. Anchor the latter to the supplied source, such as `[user-provided study design]`, while requesting page, section, figure, table, or paragraph anchors from the full source for a final verdict. Do not collapse missing metadata into a claim-support judgment.

## Status Vocabulary

- `match`: authoritative sources agree with the submitted field.
- `minor_difference`: punctuation, abbreviation, Unicode, or non-semantic formatting differs.
- `material_mismatch`: author order, title meaning, year, venue, volume, issue, article/page number, or identifier conflicts.
- `source_conflict`: authoritative sources disagree; do not choose silently.
- `not_verified`: suitable evidence was unavailable.

For claim support use `direct`, `partial`, `context_only`, `contradicted`, or `not_assessable`.

## Safety Boundary

- Do not invent a DOI, PMID, page range, author, quotation, or Zotero key.
- Do not replace a retracted or incorrect source with a merely plausible source without user confirmation.
- Do not use one aggregator as the sole truth when primary registries are available.
- Do not describe a work as currently unretracted or correction-free unless a dated status check was actually performed. Report stale or unavailable checks as `not_verified`.
- Quote only the minimum passage required and preserve page, section, figure, table, or paragraph anchors.
- Treat resolver exit `5` as a scientifically incomplete result (`single_source_only`, `not_found`, or `source_conflict`), not a successful verification. Exit `2` is invalid/offline configuration; provider network/content failures retain their structured codes.

## Output Contract

Return a summary count, then one row per reference with submitted value, canonical value, field status, sources, and correction. Follow with a claim-support matrix and a Zotero action list. Flag changes that require human confirmation.
