---
name: zju-chemistry-databases
description: Route and audit chemistry, materials, medicinal-chemistry, reaction, substance, structure, property, spectrum, safety, literature, and patent searches across appropriate public and Zhejiang University-licensed databases. Use when researchers need identity-resolved multi-database queries, SciFinder or Reaxys planning, PubChem or ChEMBL retrieval, CAS/DOI/patent linking, structure and stereochemistry checks, or evidence tables that distinguish experimental from predicted data.
---

# ZJU Chemistry Databases

Resolve chemical identity and evidence type before comparing values across databases.

## Route Gate

Classify the task as `identity`, `structure`, `property`, `bioactivity`, `reaction`, `spectrum`, `safety`, `literature`, `patent`, or `supplier`. Read `references/database-routing.md` for source selection and `references/chemical-identity.md` before merging records.

For ZJU-licensed interfaces such as SciFinder or Reaxys, use `$zju-fulltext-access` and the current library route. Authentication stays interactive; never store credentials or automate prohibited bulk extraction.

## Workflow

1. Capture the scientific question, target entities, structure representation, stereochemistry/isotopes/salts, property conditions, desired evidence type, date range, and acceptable sources.
2. Build an identity table before retrieval. Preserve user input and normalize names, formulae, InChI/InChIKey, canonical/isomeric SMILES, database IDs, and CAS Registry Numbers only when verified from an appropriate source.
   Even for an apparently obvious single-substance match, record the supplied provenance label for each identifier and explicitly state parent/mixture, neutral/charged, stereochemistry, isotope, tautomer, and salt/solvate status before declaring a match.
3. Select databases by task rather than popularity. Record query syntax or structure mode, filters, database/version, execution time, result count, and access limitations.
4. Retrieve through lawful APIs or interactive licensed interfaces. Respect rate limits, terms, export limits, and ZJU credential rules. Do not use a literature identifier as proof of a chemical identity match.
5. Normalize records without erasing provenance. Keep experimental, curated, submitted, computed, predicted, vendor, and regulatory values separate; retain units, conditions, methods, uncertainty, and source anchors.
6. Resolve conflicts using identity specificity, primary-source quality, measurement conditions, version/date, and independent corroboration. Do not average incompatible values.
7. For bioactivity, preserve target organism/protein, assay type, endpoint, units, relation operators, construct, and confidence. For reactions, preserve substrates/products, stoichiometry, conditions, yield type, and primary source.
8. Run `scripts/validate_chemistry_records.py`. Route citation verification to `$zju-reference-audit` and hazardous experimental planning to appropriate institutional safety review.

## Incomplete-Input Fallback

Do not respond only that identifiers are missing. Return an identity-resolution table that preserves every supplied token and has separate fields for parent structure, stereochemistry, isotope, charge, tautomer, salt/solvate, CAS RN, InChIKey, and isomeric SMILES. Mark unverified cells `UNKNOWN`, define the exact reconciliation and conflict rules, list acceptable provenance sources, and give the next query route. Never guess a structure from an ambiguous name.

## Minimum Response Invariants

Never collapse an identity task to a one-line yes/no answer. Return, at minimum, the identity-resolution table, one provenance row per supplied identifier, an explicit salt/stereochemistry/isotope/charge assessment, and a conflict status of `match`, `conflict`, or `not_verified`. A supplied source label is provenance metadata to preserve, not proof that the value is correct.

## Red Lines

- Never treat CAS RN, name, formula, or vendor listing alone as unambiguous identity.
- Never silently drop stereochemistry, isotope state, salt/solvate, charge, tautomer, or measurement conditions.
- Do not present predicted/computed properties as experimental.
- Do not bypass subscriptions, CAPTCHAs, access controls, export caps, or database terms.
- Do not provide a safety conclusion from a single database field.

## Output Contract

Return:

1. `Identity resolution table`.
2. `Database/query ledger`.
3. `Normalized evidence records` with stable IDs, conditions, type, and anchors.
4. `Conflict and uncertainty report`.
5. `Access or export limitations`.
6. `Recommended verification or next database route`.
\n