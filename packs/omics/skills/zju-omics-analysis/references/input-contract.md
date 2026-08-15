# Omics input contract

Use a CSV or TSV matrix with `feature_id` as the first column and one column per sample. Use a separate sample table containing at least `sample_id` and `group`; optional columns include `subject_id`, `batch`, `timepoint`, and `pair_id`.

The bundled executor supports only non-negative integer-like bulk feature counts with exactly two non-empty groups. It computes QC, log2 counts-per-million, deterministic PCA, and an exploratory Welch screen with Benjamini-Hochberg adjustment. It does not fit negative-binomial, mixed, survival, compositional, pseudobulk, or single-cell models.

Before confirmatory analysis, record:

- assay and organism;
- feature namespace and annotation release;
- biological and technical replicate definitions;
- experimental unit, pairing, blocking, batch, and repeated measures;
- primary contrast and exclusion policy;
- normalization and model family;
- multiplicity family and effect-size threshold.
