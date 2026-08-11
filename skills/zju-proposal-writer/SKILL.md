---
name: zju-proposal-writer
description: Compose, revise, or audit an evidence-grounded research proposal, opening report, grant application, doctoral research plan, internal seed project, or work-package plan. Use when Zhejiang University researchers need call-compliant structure, contribution and gap logic, hypotheses, objectives, feasibility, milestones, risk controls, budget justification, ethics/data planning, or Chinese academic proposal prose.
---

# ZJU Proposal Writer

## Mandatory Integrity Gate

Reject unsupported superlatives such as `first`, `leading`, `internationally advanced`, or their Chinese equivalents before drafting innovation prose. Replace them with a source-linked closest-prior-work comparison, a specific technical delta, and bounded verifiable wording; if evidence is absent, mark the claim `NOT_VERIFIED`. For human samples, require ethics approval, consent/data handling, and a pre-start hold without implying approval. For consistency audits, preserve supplied cross-section numbers and produce a traceability matrix even when some documents are missing.

Build the case, evidence, and executable plan before writing persuasive paragraphs.

## Intake Gate

Choose `compose`, `revise`, or `audit`. Obtain the current official call/template, scheme, language, page/word limits, evaluation criteria, deadline, applicant role, project period, budget rules, and required attachments. Treat the supplied official call as authoritative; never improvise current funding rules.

Read `references/proposal-contract.md` before drafting and `references/scheme-routing.md` for mode, stage, and compliance routing. When evidence or hypothesis artifacts are available, read `references/evidence-to-execution.md` and preserve their IDs through the proposal.

## Workflow

1. Freeze scope, decision audience, required sections, formal limits, and non-negotiable eligibility/compliance fields.
2. Build a research canon containing verified facts, preliminary evidence, constraints, available resources, and unresolved claims. Route literature gaps to `$zju-evidence-synthesis` and references to `$zju-reference-audit`.
   A count of verified sources without their records is not a usable canon: return numbered `SOURCE_RECORD_REQUIRED` rows and a claim-source schema rather than generic literature claims.
3. State the unmet need or knowledge gap without relying on vague novelty language. Link each gap to evidence and distinguish absence of evidence from evidence of absence.
4. Define the central question, bounded contribution, competing hypotheses, objectives, and measurable success criteria. Preserve predictions, falsifiers, and uncertainty from `$zju-hypothesis-design`; do not flatten competing hypotheses into one preferred narrative.
5. Convert each discriminating experiment into a work package with inputs, methods, experimental unit, outputs, dependencies, milestones, owners, and three-way `success / inconclusive / failure` decision branches. Keep methods proportional to the claim and order packages by information gained and explicit dependencies.
6. Map each work package to evidence-backed capabilities, preliminary evidence, resource constraints, and mitigation in a feasibility matrix. Distinguish scientific feasibility from resource availability and schedule feasibility; never invent pilot results, collaborations, equipment, approvals, letters, or institutional commitments.
7. Build risk, ethics, biosafety/chemical safety, data management, intellectual-property, and reproducibility plans. Flag approvals that must precede work.
8. Align schedule, budget, personnel, facilities, and deliverables. Use the official budget categories and limits supplied for the scheme.
9. Draft section contracts, then prose. Bind every literature claim to a verified citation and every project claim to the research canon. Preserve uncertainty and avoid promising guaranteed outcomes.
10. For a structured evidence/hypothesis handoff, run `python scripts/validate_proposal_manifest.py handoff.json --compile-handoff` to generate the manifest, objective-WP map, milestone decision tree, and feasibility matrix. Validate the compiled manifest, then conduct reviewer-style checks for significance, discrimination, feasibility, differentiation, and compliance before formatting prose.

## Incomplete-Input Fallback

Treat an availability flag as different from the call text itself. Always return an official-call extraction table with source/version, scheme, eligibility, deadline/time zone, period, budget cap/categories, mandatory sections, evaluation criteria, attachments, and unresolved fields. Convert eligibility and deadline into explicit pass/block gates. If the call text is absent, mark compliance `NOT_VERIFIED` and continue only with a generic objective-to-work-package scaffold and compliance checklist; never imply call-specific compliance.

## Red Lines

- Never fabricate citations, preliminary data, collaborators, facilities, approvals, budget rules, or prior awards.
- Do not recycle confidential proposal text without permission or conceal material overlap.
- Do not frame speculative outcomes as deliverables already demonstrated.
- Do not claim compliance with a call that was not supplied or freshly verified.

## Output Contract

Return:

1. `Call/scheme and scope contract`.
2. `Research canon and evidence table`.
3. `Gap, contribution, hypothesis, and objective map`.
4. `Objective-to-WP map, ordered work packages, milestone decision tree, feasibility matrix, and risks`.
5. Draft proposal in the requested structure.
6. `Compliance and unresolved-field checklist`.
7. `Review scorecard and next revision action`.
