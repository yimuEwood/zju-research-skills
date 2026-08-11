# Claim-evidence ledger

Use one record per atomic claim:

```json
{
  "claim_id": "C-001",
  "claim": "The treatment reduced the primary outcome at day 14.",
  "claim_type": "author_result",
  "evidence_ids": ["FIG-2B", "DATA-primary-14d"],
  "source_anchor": "Results paragraph 2; Fig. 2b",
  "citation_verified": true,
  "status": "supported",
  "scope_notes": "Applies to the prespecified analysis population."
}
```

Allowed evidence classes: `author_data`, `author_method`, `verified_literature`, `policy_or_standard`, and `clearly_labeled_inference`. Use statuses `supported`, `partial`, `contradicted`, `unverified`, or `placeholder`.

A verified citation identifier establishes source identity, not claim support. Literature claims need a source anchor and support assessment. Author-result claims need a data, figure, table, or analysis anchor.
