---
name: zju-research-integrity
description: Audit research workflows, manuscripts, data, images, code, authorship, citations, AI use, ethics, conflicts, and provenance for integrity risks and remediation. Use when Zhejiang University researchers need preventive checks, an evidence-preservation plan, authorship or data-governance clarification, image/statistical consistency review, disclosure language, or neutral triage of a suspected problem. This skill does not determine misconduct or replace formal institutional procedures.
---

# ZJU Research Integrity

Prevent, document, and triage integrity risks without making unsupported accusations or legal findings.

## Mode Gate

Choose `preventive_audit`, `manuscript_audit`, `data_image_code_audit`, `authorship_disclosure`, `ai_use_audit`, or `incident_triage`. For current ZJU rules, read `references/zju-policy-routing.md` and verify the live official text before relying on a requirement. Read `references/integrity-checklist.md` for audits and `references/incident-triage.md` for suspected incidents.

## Workflow

1. Define scope, roles, data sensitivity, applicable institution/funder/journal rules, decision owner, and whether the task is preventive or incident-related.
2. Preserve source artifacts, versions, timestamps, hashes, approvals, contributor records, analysis environments, and communication boundaries. Do not alter originals or conduct covert surveillance.
3. Map claims to data, code, images, statistics, citations, approvals, and contributors. Run `scripts/audit_provenance_manifest.py` on a structured manifest.
4. For an incident package, run `python scripts/triage_integrity_case.py incident.json --check all`, or select `neutral_triage`, `scope_severity`, or `remediation_escalation`. Keep observations, reported statements, anomalies, and interpretations separate; compute severity from explicit signals; require the least disruptive preservation and authorized-referral actions appropriate to that severity.
5. Check fabrication/falsification indicators, selective reporting, inappropriate image processing, duplicated or inconsistent records, statistical/design defects, citation accuracy, plagiarism/overlap, authorship/contribution, conflicts, data/material provenance, ethics/consent, safety, and AI/tool disclosure.
6. Separate fact, anomaly, missing documentation, interpretation, and allegation. Seek benign explanations and domain expertise without erasing evidence.
7. Grade findings as `documentation`, `correctable`, `serious`, or `urgent_safety_or_legal`, with rationale and affected outputs. A triage level is not a misconduct verdict.
8. Propose the least disruptive valid remediation: documentation repair, correction, reanalysis, figure replacement, authorship discussion, disclosure, submission hold, or referral to the authorized institutional route.
9. For incident triage, protect confidentiality, avoid retaliation, minimize personal data, and use current official channels. Do not confront or notify third parties unless the user has authority and the procedure requires it.
10. Close with an action owner, deadline, preserved evidence list, decisions, unresolved questions, and re-audit trigger.

## Red Lines

- Never determine guilt, intent, authorship entitlement, plagiarism, or legal liability from incomplete evidence.
- Never fabricate policy text, approvals, consent, contributor roles, dates, or audit trails.
- Do not destroy, rewrite, or selectively clean original data, images, code, or logs.
- Do not expose whistleblower identities, sensitive personal data, credentials, or unpublished work.
- Do not use automated similarity, image, or statistical flags as final findings without expert review.

## Output Contract

Return:

1. `Scope, rule sources, and decision boundary`.
2. `Preservation/provenance manifest`.
3. `Finding table` separating facts, anomalies, missing evidence, and interpretations.
4. `Severity and affected outputs`.
5. `Remediation and disclosure plan`.
6. `Escalation options` based on freshly verified official routes.
7. `Owners, deadlines, unresolved questions, and re-audit trigger`.
