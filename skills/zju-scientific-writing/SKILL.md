---
name: zju-scientific-writing
description: Draft, substantively revise, or language-polish evidence-bound scientific manuscripts in Chinese or English while preserving the author's data, numerical meaning, uncertainty, and verified citations. Use for section drafting, argument construction, restructuring, revision, translation, concision, journal-style polishing, claim-by-claim checks, or keeping Abstract, Results, figures, tables, supplements, and reviewer responses aligned to a canonical result registry. Always select explicit draft, revise, or polish mode; never introduce an untraceable fact or reference.
---

# ZJU Scientific Writing

Write from an evidence ledger. Improve reasoning and language without manufacturing results, certainty, novelty, mechanisms, references, or compliance claims.

## Select a Mode

- `draft`: construct a section or manuscript from author-supplied data, methods, outline, and verified literature.
- `revise`: change scientific organization, argument, scope, or interpretation in an existing manuscript; record claim and result impacts explicitly.
- `polish`: improve existing prose while preserving scientific meaning, numeric values, direction, uncertainty, and citation scope.

State the mode at the start. If a request mixes them, separate the drafted passages from edits to supplied text. Read `references/writing-modes.md` and `references/claim-evidence-ledger.md`.

## Workflow

1. Define document type, audience/journal, language, section, word limit, terminology, and allowed evidence. Identify required information that is absent.
2. Build or validate a claim-evidence ledger before prose. Each factual claim must bind to author data, a method/protocol, or a verified source. Author-result claims must bind to stable `result_id` values from `$zju-statistics-audit`, not only to a nearby figure label. Run the local ledger checker when JSON is available:

   `python scripts/check_claim_ledger.py --input claims.json --output ledger-report.json`

3. For a new manuscript, a major rewrite, or any request to make work “submission-ready,” read `references/contribution-contract.md`. Confirm the contributions, their evidence, and claim boundaries before substantive drafting. Map every major Results subsection to at least one confirmed contribution and its evidence. Validate structured JSON with:

   `python scripts/check_writing_contract.py --input writing-contract.json --output writing-contract-report.json`

4. In `draft` mode, build the argument in this order: question/gap, approach, result with magnitude and uncertainty, interpretation within design, limitation, implication. Keep methods reproducible and results separate from discussion. A metric-only Results subsection without a contribution/evidence mapping remains incomplete.
5. In `revise` mode, make a section-level change plan, record which claim/result IDs each move affects, revise the argument, and emit before/after claim scope plus follow-up edits required in the Abstract, figures/tables, Methods, supplement, and response letter.
6. In `polish` mode, create an invariant list for numbers, units, group labels, direction, statistical qualifiers, gene/protein/chemical notation, and citations. Edit against those invariants and report any substantive change separately.
   When structured assertion ledgers exist, run `python scripts/validate_mode_fidelity.py writing-mode.json`. In `polish`, preserve the exact assertion set and factual signatures. In `draft`, require every output assertion to bind to verified literature or author data.
7. Reconcile every numerical use against the canonical result registry with `$zju-statistics-audit/scripts/reconcile_result_registry.py`. Never independently retype a rounded value into the Abstract, Results, legend, table, supplement, or response letter.
   When figure, review, or data-package artifacts are present, also run `$zju-statistics-audit/scripts/validate_result_handoff.py` so claim, evidence, and result IDs resolve across the package.
8. Audit every sentence for evidence, scope, causality, statistical wording, and citation placement. Invoke `$zju-reference-audit` for unresolved references and `$zju-statistics-audit` for statistical claims.
9. Before using `submission_ready`, create a reviewer-objection register covering novelty, validity, scope, reproducibility, statistics, and editorial fit. All objections must be resolved, accepted as bounded limitations, or explicitly open; open objections block the readiness label.
10. Return the revised text plus a change/evidence report. Mark placeholders such as `[AUTHOR DATA REQUIRED]` rather than filling gaps plausibly.

For a finalized source-complete artifact, render constrained Markdown or structured JSON plus an optional canonical result registry into a real DOCX:

`python scripts/build_research_docx.py --source manuscript.json --registry result-registry.json --output manuscript.docx`

The builder resolves result tokens, requires stable citation/source anchors, rejects unresolved placeholders, writes real headings and numbering, records hashes, and reopens the OOXML package. A successful package check is not visual page-layout review; render and inspect the DOCX before submission.

## Missing-Text Fallback

If the source passage or author data is absent, do not produce revised prose and do not stop at a bare request. Return: selected mode; the exact input still needed; an invariant register derived from the user's constraints; a pre-commit statement that preservation is `not_yet_verifiable`; and a material-change log template. For a polish request, include rows for numbers, units, group labels, direction, significance/P values, technical notation, and citations. Mark every check `pending_source_text` until the original and revised passages can be compared.

## Non-Negotiable Rules

- Do not add a citation, fact, number, effect, method detail, ethical approval, data-availability statement, or author contribution that was not supplied and verified.
- Do not change increase to decrease, significant to non-significant, association to causation, or a bounded conclusion to a universal one.
- Do not hide contradictory results or limitations to make prose more persuasive.
- Do not broaden a confirmed contribution during drafting. Record a proposed change and revalidate its evidence and boundary first.
- Do not imitate copyrighted text or an identifiable living author's exact style. Use general high-impact scientific qualities: precise, economical, evidence-led, and appropriately cautious.

## Output Contract

Return: mode and scope, evidence-bound text, contribution-to-results map, claim-to-`result_id` map, cross-artifact follow-up map for revise mode, unresolved placeholders, claim-evidence exceptions, result-registry consistency status, reviewer-readiness status when requested, and material-change log. When artifact generation is requested and inputs are complete, also return the DOCX and its manifest; keep OOXML package verification separate from visual render QA. For polish mode, explicitly confirm whether numbers, direction, significance, units, and citations were preserved.
