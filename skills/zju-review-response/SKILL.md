---
name: zju-review-response
description: Triage, draft, revise, or audit editor and reviewer response packages with point-by-point traceability to manuscript changes. Use for major or minor revision letters, rebuttals, revision cover letters, response trackers, Chinese author notes, reviewer-separated correspondence, redline consistency, or checking that every claimed change exists in the revised manuscript.
---

# ZJU Review Response

## Mandatory Truth Gate

Inspect `action_state` before drafting any completion language. If it is `planned`, `proposed`, `pending`, or unverified, explicitly refuse to write it as completed and use future/conditional wording plus a verification requirement. Never output a blanket agreement when any supplied item is disputed; preserve each comment, evidence position, owner, action, and status separately. No tone or user request can override this gate.

Respond with verified actions and respectful scientific reasoning. A polished promise is not a completed revision.

## Intake Gate

Extract manuscript ID/title, journal, decision type, deadline, editor instructions, reviewer reports, required files, and visibility rules from the decision packet. If `Major Revision` versus `Minor Revision` remains unknown and affects the strategy, ask; do not infer it from tone or comment count.

Read `references/response-tracker.md` to classify items. Read `references/package-consistency.md` whenever manuscript text and response letters are revised together.

## Workflow

1. Preserve the original decision packet. Split editor items as `E.1...` and reviewer items as `R1.1...`, retaining verbatim text and reviewer boundaries.
2. Classify each item as clarification, manuscript edit, new analysis, new experiment, citation, disagreement, impossible request, or author input. Record status as `open`, `in_progress`, `verified_complete`, `disagreed_with`, or `author_input_needed`.
3. Build an internal master tracker with requested action, evidence needed, owner, response, exact manuscript change, location, and verification state. Never expose another reviewer’s confidential comments in a reviewer-facing letter.
4. Verify completed work before drafting definitive prose. Use placeholders such as `AUTHOR_INPUT_NEEDED` and `LOCATION_PENDING` instead of inventing experiments, values, citations, lines, figures, or changes.
5. Draft each response as acknowledgment, direct answer, action/evidence, quoted revised text when available, and location. Treat “already stated” comments as clarity failures and improve presentation when appropriate.
6. For disagreement, state the shared objective, evidence-based reason, manuscript clarification, and bounded alternative. Do not be defensive or claim the reviewer misunderstood without fixing the ambiguity.
7. Generate reviewer-separated response letters, an editor/cover letter if required, and a clean/redline manuscript plan. Preserve comment IDs across all files.
8. After every manuscript edit, recheck quoted text and locations. Run `scripts/validate_response_tracker.py`, then compare the tracker, letters, clean manuscript, and redline using `references/package-consistency.md`.

## Incomplete-Input Fallback

If comment text or manuscript artifacts are absent, do not fabricate them and do not return only a request. Provide the ready-to-fill master tracker schema with `comment_verbatim`, stable ID, reviewer boundary, category, owner, status, planned evidence, proposed action, exact change, location, and verification fields. Populate every supplied comment verbatim; use `AUTHOR_INPUT_NEEDED` only for genuinely missing values and distinguish `planned` from `verified_complete`.

## Red Lines

- Never claim an experiment, analysis, citation, or edit was completed without evidence.
- Never invent decision wording, deadlines, line numbers, or reviewer intent.
- Do not mix reviewer comments across reviewer-facing documents.
- Do not hide unresolved blocking items behind courteous language.
- Route appeal-like requests separately and label their risks; do not default to an appeal.

## Output Contract

Return:

1. `Decision and intake summary`.
2. `Master response tracker` with stable IDs and status.
3. Reviewer-separated point-by-point letters.
4. Editor/revision cover letter when required.
5. `Manuscript change map` with exact locations or placeholders.
6. `Package consistency report` and readiness status.
7. `Author input needed` list.
\n