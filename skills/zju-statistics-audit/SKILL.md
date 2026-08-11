---
name: zju-statistics-audit
description: Audit experimental design, statistical analysis, figures, captions, and cross-section numerical consistency in manuscripts and reports. Use to check experimental units, biological versus technical replicates, sample size, effect sizes, confidence intervals, model assumptions, multiplicity, missing data, figure encodings, and statistical wording. This is a structured audit, not a substitute for domain or clinical statistical responsibility.
---

# ZJU Statistics Audit

Start from the design and estimand, not from a preferred test. Trace every reported number back to its analysis population, experimental unit, model, and uncertainty.

## Workflow

1. Define the scientific question, outcome, estimand, experimental unit, assignment mechanism, grouping structure, repeated measures, and analysis population. If these are missing, flag them before judging tests.
2. Read `references/audit-checklist.md`. Map biological and technical replicates, independence, clustering, pairing, exclusions, missingness, stopping rules, and sample-size rationale.
3. Reconstruct each primary analysis: variables, preprocessing, model/test, covariates, interactions, assumptions, multiplicity family, effect estimate, uncertainty, and software/version.
4. Check whether assumptions were evaluated and whether the method matches scale, design, nesting, repeated measures, censoring, and distribution. Recommend alternatives conditionally; do not prescribe a test without sufficient design information.
5. Audit effect sizes, confidence intervals, exact sample counts, exact or bounded P values, multiple-comparison control, sensitivity analyses, and missing-data handling.
6. Cross-check abstract, methods, results, tables, figures, captions, supplement, and data/code for number, direction, denominator, unit, label, and significance consistency.
7. Use `scripts/scan_reporting.py` only as a first-pass offline triage. Manually verify every warning and inspect issues the pattern scanner cannot detect.
8. Report critical, major, and minor findings using `references/reporting-template.md`, with evidence anchor, consequence, and specific repair.

Always emit an explicit severity for each finding. Classify pseudoreplication, wrong experimental unit, fabricated or impossible values, undisclosed outcome switching, and analysis-population errors that can reverse the primary conclusion as `critical`; explain when context lowers the severity. A correct diagnosis without severity and repair is incomplete.

## Integrity and Scope

- Do not infer independence from sample count. Identify the true unit of replication.
- Do not equate non-significance with equivalence or absence of effect.
- Do not report a post hoc subgroup as confirmatory without disclosure.
- Do not invent raw data, sample exclusions, power calculations, effect sizes, intervals, or corrected P values.
- For clinical, regulatory, or high-stakes decisions, require review by a qualified statistician and applicable governance.

## Output Contract

Return: design map, analysis inventory, issue table, cross-section consistency table, required author queries, and a prioritized repair plan. Keep automated heuristic findings labeled `triage_only` until manually confirmed.
