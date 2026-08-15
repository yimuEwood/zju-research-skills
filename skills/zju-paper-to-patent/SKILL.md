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

Read `references/source-to-claim.md` before invention mining and `references/disclosure-structure.md` before drafting. When using Paper Cards or experiment records, read `references/research-to-invention-map.md` and preserve their source, artifact, run, and result IDs.

## Workflow

1. Preserve the source packet and assign stable IDs: `P` text, `E` equations, `F` figures, `C` code, `X` experiments, and `N` inventor notes. Record provenance and confidentiality.
2. Extract technical problems, effects, components/steps, parameter ranges, dependencies, alternatives, and evidence. Use only `explicit`, `inherent`, `needs_confirmation`, or `unsupported` as support states.
   Before interpretation, copy every supplied number, unit, range, condition, and anchor verbatim into the ledger and perform a digit-by-digit invariant check against the source excerpt. A changed duration, concentration, dimension, performance value, or condition is a blocking integrity failure.
3. Build a terminology ledger and feature-evidence matrix. Exclude `unsupported` features from formal claims; keep questions outside claims as `[TO CONFIRM: ...]`.
   Do not add a mechanism, effect, component, or design option merely because it is plausible. Author-stated speculation stays `needs_confirmation`; agent-generated possibilities stay `unsupported` and out of invention concepts and claims.
4. Build four linked maps: problem-solution-effect, feature-evidence, claim dependency, and prior-art queries. Separate scientific contribution from the proposed technical solution; bind every asserted effect to tested conditions and evidence IDs.
5. Develop source-supported alternative embodiments by stating the replaced feature, alternative feature, retained target effect, and discriminating evidence. Keep unsupported alternatives as inventor questions outside claim candidates.
6. Generate prior-art query blocks from problem, solution, effect, synonym, and classification terms with dates and target sources. Distinguish a search hit from verified document facts and from professional novelty/inventive-step conclusions.
7. Draft the independent claim concept first, then dependent features, specification/disclosure, embodiments, figures, and abstract so terminology and step order agree. Preserve formula meaning and define every symbol. Treat the dependency graph as a drafting outline, not a legal determination of claim scope.
8. Generate claim-aligned flowcharts and diagrams from supported steps. Illustrations must not add components absent from the source/evidence ledger.
9. Run `scripts/validate_feature_ledger.py`; for `workflow_version: "2.0"`, inspect the returned four maps, dependency order, query coverage, embodiment evidence gaps, and `invention_map_ready` before drafting.
10. Run `python scripts/audit_claim_map.py claim-map.json` to verify dependency order and that every dependent claim adds a sourced limiting feature.
11. Run `python scripts/separate_prior_art.py prior-art-lanes.json` to keep invention sources, pre-critical-date candidates, post-cutoff background, and unresolved dates in separate lanes. Do not put novelty, patentability, or freedom-to-operate conclusions in this technical map.
12. Deliver a clearly labelled `drafting aid` to the inventor, ZJU technology-transfer/patent office route, or patent professional. Do not promise filing success, scope, freedom to operate, or noninfringement.

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
3. `Problem-solution-effect map` and source-supported alternative embodiments.
4. `Claim-dependency drafting map` and unresolved inventor questions.
5. `Prior-art query map and search ledger`, not a legal conclusion.
6. Chinese technical disclosure or structured claim/specification draft as requested.
7. Claim-aligned figure/formula plan.
8. `Support, discrimination, and consistency audit` plus professional-review handoff.
