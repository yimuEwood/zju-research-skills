---
name: zju-paper-to-patent
description: Convert a paper, thesis, report, source code, figures, experiment records, or inventor notes into an evidence-grounded Chinese technical disclosure or patent-draft aid. Use for invention-point mining, source-to-feature mapping, prior-art comparison, claim-set preparation, formula and flowchart alignment, paper-versus-patent audits, or attorney-facing disclosure packages. This skill does not provide a patentability or legal opinion.
---

# ZJU Paper to Patent

## Mandatory Inventorship and Disclosure Gate

Authorship is never inventorship evidence. If asked to infer inventors from an author list, explicitly refuse to name anyone, request claim-by-claim conception contributions, record unresolved candidates without names, state jurisdiction dependence, and require patent-professional review. For any public disclosure, record the exact event/date, state that consequences depend on jurisdiction, make no grace-period assumption, and escalate promptly to authorized patent counsel or the institutional technology-transfer route.

Preserve technical support and confidentiality before drafting claims. Patent professionals and authorized institutional processes remain the legal review gate.

## Intake Gate

Choose `invention_mining`, `technical_disclosure`, `claim_set`, `draft_audit`, or `paper_patent_comparison`. Confirm source confidentiality/publication state, target jurisdiction/language, intended applicants/inventors as user-provided facts, filing timeline, existing disclosures, and attorney/technology-transfer handoff. Do not upload unpublished material to external services without authorization.

Read `references/source-to-claim.md` before invention mining and `references/disclosure-structure.md` before drafting.

## Workflow

1. Preserve the source packet and assign stable IDs: `P` text, `E` equations, `F` figures, `C` code, `X` experiments, and `N` inventor notes. Record provenance and confidentiality.
2. Extract technical problems, effects, components/steps, parameter ranges, dependencies, alternatives, and evidence. Use only `explicit`, `inherent`, `needs_confirmation`, or `unsupported` as support states.
   Before interpretation, copy every supplied number, unit, range, condition, and anchor verbatim into the ledger and perform a digit-by-digit invariant check against the source excerpt. A changed duration, concentration, dimension, performance value, or condition is a blocking integrity failure.
3. Build a terminology ledger and feature-evidence matrix. Exclude `unsupported` features from formal claims; keep questions outside claims as `[TO CONFIRM: ...]`.
   Do not add a mechanism, effect, component, or design option merely because it is plausible. Author-stated speculation stays `needs_confirmation`; agent-generated possibilities stay `unsupported` and out of invention concepts and claims.
4. Separate scientific contribution from potentially protectable technical solution. Identify technical effect, implementation sequence, required features, optional embodiments, and realistic design-arounds without asserting novelty.
5. Plan prior-art searching across literature and patent sources with dates, jurisdictions, classifications, queries, and stable identifiers. Distinguish located documents from verified legal status and from professional novelty/inventive-step conclusions.
6. Draft the independent claim concept first, then dependent features, specification/disclosure, embodiments, figures, and abstract so terminology and step order agree. Preserve formula meaning and define every symbol.
7. Generate claim-aligned flowcharts and diagrams from supported steps. Illustrations must not add components absent from the source/evidence ledger.
8. Run `scripts/validate_feature_ledger.py`; check support, antecedent basis, terminology, units, ranges, claim/specification alignment, figure references, and unresolved confidential facts.
9. Deliver a clearly labelled `drafting aid` to the inventor, ZJU technology-transfer/patent office route, or patent professional. Do not promise filing success, scope, freedom to operate, or noninfringement.

## Incomplete-Input Fallback

Do not answer only with a request for the paper. Return an empty but operational feature/evidence ledger with feature ID, verbatim feature, support state, source type, exact anchor, technical effect, confidentiality, and inventor-question fields. Populate all supplied excerpts and facts; mark absent anchors `SOURCE_REQUIRED`. Also provide the support-state rules, prior-art plan, and professional handoff gate without asserting novelty or patentability.

## Minimum Response Invariants

Even for a short excerpt, return the feature/evidence ledger rather than a prose-only list. Every populated row must repeat its exact source anchor and preserve all numeric literals and units. End with an explicit `source invariant check` listing each source number and copied value; if any pair differs, stop and correct it before delivery.

## Red Lines

- Never infer inventorship, ownership, publication dates, priority, novelty, legal status, or filing eligibility.
- Never fabricate embodiments, performance, parameter ranges, examples, citations, or prior art.
- Do not broaden a claim beyond technical support merely for apparent coverage.
- Do not expose unpublished material, credentials, or controlled technical information.

## Output Contract

Return:

1. `Source and confidentiality inventory`.
2. `Feature/evidence ledger` with support states.
3. `Invention concepts and unresolved inventor questions`.
4. `Prior-art search ledger`, not a legal conclusion.
5. Chinese technical disclosure or structured claim/specification draft as requested.
6. Claim-aligned figure/formula plan.
7. `Support and consistency audit` plus professional-review handoff.
