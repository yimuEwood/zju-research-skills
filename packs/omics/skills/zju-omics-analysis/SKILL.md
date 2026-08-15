---
name: zju-omics-analysis
description: Validate and screen bulk transcriptomic or other feature-by-sample count matrices, sample metadata, differential signals, and reproducible omics handoffs. Use for CSV/TSV count matrices, library-size and sample-QC checks, exploratory PCA, two-group differential screening, batch or subject metadata audits, and routing to DESeq2, edgeR, limma, Scanpy, or domain specialists. Do not use its lightweight screen as a substitute for a preregistered production differential-expression model.
---

# ZJU Omics Analysis

Produce an auditable omics intake and exploratory screen without upgrading it into confirmatory evidence.

## Workflow

1. Identify assay, organism, feature namespace, matrix scale, experimental unit, group, batch, subject, pairing, and repeated-measure structure. Keep missing design facts explicit.
2. For a feature-by-sample raw-count matrix and sample sheet, run:

   `python scripts/omics_screen.py --matrix counts.csv --samples samples.csv --output omics-screen.json`

   Read `references/input-contract.md` before adapting another assay or matrix orientation.
3. Inspect source hashes, sample alignment, count validity, library sizes, retained features, PCA variance, group separation, batch warnings, and differential-screen limitations.
4. Treat the bundled Welch screen on log-CPM as exploratory triage only. Route paired, repeated, blocked, count-model, single-cell, multi-omic, pathway, or compositional analyses to the named external executor in `references/executor-routing.md`.
5. Pass feature identifiers, transformations, design columns, excluded samples/features, software versions, hashes, and unresolved design issues to `$zju-statistics-audit` and `$zju-scientific-figure`.

## Required boundaries

- Never infer sample groups from filenames or reorder mismatched samples silently.
- Never describe PCA separation or an unadjusted screen as biological validation.
- Preserve raw counts; do not overwrite the source matrix.
- Require species and identifier namespace before enrichment or pathway interpretation.
- Reject negative, non-finite, duplicated, or non-integer raw counts and unsupported experimental designs.

## Output

Return `omics-screen.json`, a concise QC interpretation, the supported next executor, and all blocking design questions. Label every bundled differential result `exploratory_screen`.
