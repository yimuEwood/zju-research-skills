---
name: zju-statistics-audit
description: Design, run a bounded supported core of, or audit experiment-linked statistical analyses and reconcile results across manuscripts, figures, tables, supplements, and reviewer responses. Use to create an analysis contract; run prespecified Welch or paired t tests, Pearson correlation, or simple OLS from local tables; choose methods for hierarchical, repeated, clustered, missing, censored, high-dimensional, agricultural, chemical, materials, or biomedical data; or check experimental units, replicates, effect sizes, uncertainty, diagnostics, multiplicity, robustness, and numerical consistency. This workflow does not replace domain or clinical statistical responsibility.
---

# ZJU Statistics Audit

Start from the design and estimand, not from a preferred test. Trace every reported number back to its analysis population, experimental unit, model, and uncertainty.

## Workflow

1. Define the scientific question, outcome, estimand, experimental unit, assignment mechanism, grouping structure, repeated measures, and analysis population. If these are missing, flag them before judging tests. Consume the design handoff from `$zju-experiment-log` when available.
2. Read `references/audit-checklist.md`. Map biological and technical replicates, independence, clustering, pairing, exclusions, missingness, stopping rules, and sample-size rationale.
3. For planning or reanalysis, read `references/analysis-decision-map.md` and create a versioned analysis contract. Separate primary, secondary, exploratory, QC, and sensitivity analyses. Validate it with:

   `python scripts/validate_analysis_contract.py --input analysis-contract.json --output analysis-contract-report.json`

   When the frozen contract maps to the shipped execution core, add an `execution` object as defined in `references/execution-contract.md`, then execute the local table rather than hand-copying model output:

   `python scripts/execute_analysis.py --data measurements.csv --contract analysis-contract.json --output-dir analysis-output`

   The shipped executor currently supports `welch_ttest`, `paired_ttest`, `pearson_correlation`, and `simple_ols`. It emits `analysis-run.json` and a canonical `result-registry.json`, binds both input hashes and the executor hash, applies declared Holm or Benjamini-Hochberg correction for multi-test families, and rejects unsupported models explicitly. Do not silently simplify a mixed, clustered, survival, count, multivariate, Bayesian, omics, or other unsupported model into this core.

4. Reconstruct or inspect each executed analysis: variables, preprocessing, model, covariates, interactions, dependence terms, assumptions, multiplicity family, effect estimate, uncertainty, software/version, diagnostics and their consequences, missing-data strategy, and sensitivity analyses. A numerically successful run is not evidence that the model matches the design.
5. Check whether the method represents the estimand, outcome scale, design, nesting, repeated measures, censoring, distribution, and domain-specific measurement process. Recommend alternatives conditionally; do not prescribe a test without sufficient design information.
6. Audit effect sizes, confidence intervals, experimental-unit and observation counts, exact or bounded P values, multiple-comparison control, diagnostic results, sensitivity analyses, and missing-data assumptions.
7. Read `references/result-registry.md`. Register each reportable result once with a stable `result_id`, then map every figure, table, Abstract/Results statement, supplement, and reviewer response back to it. Reconcile all rendered values with:

   `python scripts/reconcile_result_registry.py --input result-registry.json --analysis-contract analysis-contract.json --output consistency-report.json`

   When outputs can use templates, render values directly rather than copying them:

   `python scripts/render_result_tokens.py --registry result-registry.json --template results-template.md --output results.md --uses-output rendered-uses.json`

   For a manuscript package, validate the shared claim/result/evidence IDs across writing, figure, review, and data inventories with:

   `python scripts/validate_result_handoff.py --input result-handoff.json --output result-handoff-report.json`

8. Use `scripts/scan_reporting.py` only as a first-pass offline triage. Manually verify every warning and inspect issues the pattern scanner cannot detect.
9. Report critical, major, and minor findings using `references/reporting-template.md`, with evidence anchor, consequence, smallest valid repair, and required reanalysis or sensitivity check.

Always emit an explicit severity for each finding. Classify pseudoreplication, wrong experimental unit, fabricated or impossible values, undisclosed outcome switching, and analysis-population errors that can reverse the primary conclusion as `critical`; explain when context lowers the severity. A correct diagnosis without severity and repair is incomplete.

## Integrity and Scope

- Do not infer independence from sample count. Identify the true unit of replication.
- Do not equate non-significance with equivalence or absence of effect.
- Do not report a post hoc subgroup as confirmatory without disclosure.
- Do not invent raw data, sample exclusions, power calculations, effect sizes, intervals, or corrected P values.
- For clinical, regulatory, or high-stakes decisions, require review by a qualified statistician and applicable governance.

## Output Contract

Return: design map, validated analysis contract or reconstruction, `analysis-run.json` when the shipped core was executed, canonical result registry, diagnostic/sensitivity matrix, issue table, cross-artifact consistency report, required author queries, and a prioritized repair plan. Keep automated heuristic findings labeled `triage_only` until manually confirmed.
