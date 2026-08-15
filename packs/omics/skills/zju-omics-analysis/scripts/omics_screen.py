#!/usr/bin/env python3
"""Deterministic QC, PCA, and exploratory two-group screening for count matrices."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


VERSION = "1.0"


class OmicsInputError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_table(path: Path) -> pd.DataFrame:
    separator = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    try:
        return pd.read_csv(path, sep=separator)
    except Exception as exc:
        raise OmicsInputError(f"cannot parse table {path}: {exc}") from exc


def _bh_adjust(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranked = values[order]
    adjusted = np.empty_like(ranked, dtype=float)
    running = 1.0
    total = len(values)
    for index in range(total - 1, -1, -1):
        running = min(running, ranked[index] * total / (index + 1))
        adjusted[index] = running
    restored = np.empty_like(adjusted)
    restored[order] = np.clip(adjusted, 0.0, 1.0)
    return restored


def _orient_components(scores: np.ndarray, loadings: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    for component in range(loadings.shape[1]):
        pivot = int(np.argmax(np.abs(loadings[:, component])))
        if loadings[pivot, component] < 0:
            loadings[:, component] *= -1
            scores[:, component] *= -1
    return scores, loadings


def execute(matrix_path: Path, samples_path: Path, *, min_total: int, min_samples: int) -> dict:
    matrix = _read_table(matrix_path)
    samples = _read_table(samples_path)
    if matrix.empty or matrix.columns[0] != "feature_id":
        raise OmicsInputError("matrix must start with a feature_id column")
    required = {"sample_id", "group"}
    if not required <= set(samples.columns):
        raise OmicsInputError("sample table requires sample_id and group columns")
    if matrix["feature_id"].isna().any() or matrix["feature_id"].astype(str).duplicated().any():
        raise OmicsInputError("feature_id values must be non-empty and unique")
    sample_ids = samples["sample_id"].astype(str).tolist()
    if any(not value.strip() or value == "nan" for value in sample_ids) or len(sample_ids) != len(set(sample_ids)):
        raise OmicsInputError("sample_id values must be non-empty and unique")
    matrix_samples = [str(column) for column in matrix.columns[1:]]
    if set(matrix_samples) != set(sample_ids):
        missing = sorted(set(sample_ids) - set(matrix_samples))
        extra = sorted(set(matrix_samples) - set(sample_ids))
        raise OmicsInputError(f"sample mismatch; missing={missing}, extra={extra}")
    matrix = matrix[["feature_id", *sample_ids]]
    try:
        counts = matrix[sample_ids].apply(pd.to_numeric, errors="raise").to_numpy(dtype=float)
    except Exception as exc:
        raise OmicsInputError("count cells must be numeric") from exc
    if not np.isfinite(counts).all() or (counts < 0).any():
        raise OmicsInputError("counts must be finite and non-negative")
    if not np.allclose(counts, np.rint(counts), atol=1e-8):
        raise OmicsInputError("raw counts must be integer-like")
    groups = samples.set_index("sample_id").loc[sample_ids, "group"].astype(str)
    if any(not value.strip() or value == "nan" for value in groups):
        raise OmicsInputError("group values must be non-empty")
    group_names = sorted(groups.unique().tolist())
    if len(group_names) != 2 or any(int((groups == group).sum()) < 2 for group in group_names):
        raise OmicsInputError("the exploratory executor requires exactly two groups with at least two samples each")
    for design_column in ("subject_id", "pair_id", "cluster_id"):
        if design_column not in samples.columns:
            continue
        values = samples.set_index("sample_id").loc[sample_ids, design_column]
        if values.isna().any() or any(not str(value).strip() for value in values):
            raise OmicsInputError(f"{design_column} must be complete when supplied")
        if values.astype(str).duplicated().any():
            raise OmicsInputError(
                f"repeated {design_column} detected; paired, repeated, or clustered designs require a design-aware executor"
            )
    warnings = []
    if "batch" not in samples.columns:
        warnings.append("batch metadata not supplied")
    else:
        batch = samples.set_index("sample_id").loc[sample_ids, "batch"]
        if batch.isna().any() or any(not str(value).strip() for value in batch):
            raise OmicsInputError("batch must be complete when supplied")
        batch = batch.astype(str)
        if batch.nunique() > 1:
            cross = pd.crosstab(groups, batch)
            if (cross == 0).any().any():
                raise OmicsInputError(
                    "batch is confounded with group; the bundled unadjusted screen must not be run"
                )
            warnings.append("multiple balanced batches detected; batch was audited but is not modeled by the exploratory screen")
    library_sizes = counts.sum(axis=0)
    if (library_sizes <= 0).any():
        raise OmicsInputError("every sample must have a positive library size")
    keep = (counts.sum(axis=1) >= min_total) & ((counts > 0).sum(axis=1) >= min_samples)
    if int(keep.sum()) < 2:
        raise OmicsInputError("fewer than two features remain after filtering")
    filtered = counts[keep]
    features = matrix.loc[keep, "feature_id"].astype(str).tolist()
    log_cpm = np.log2((filtered / library_sizes[np.newaxis, :]) * 1_000_000.0 + 1.0)

    centered = log_cpm.T - log_cpm.T.mean(axis=0, keepdims=True)
    u, singular, vt = np.linalg.svd(centered, full_matrices=False)
    components = min(2, len(singular))
    scores = u[:, :components] * singular[:components]
    loadings = vt[:components].T.copy()
    scores, loadings = _orient_components(scores, loadings)
    variance = singular**2
    variance_ratio = variance[:components] / variance.sum() if variance.sum() else np.zeros(components)

    first = np.flatnonzero(groups.to_numpy() == group_names[0])
    second = np.flatnonzero(groups.to_numpy() == group_names[1])
    a = log_cpm[:, first]
    b = log_cpm[:, second]
    estimate = b.mean(axis=1) - a.mean(axis=1)
    test = stats.ttest_ind(b, a, axis=1, equal_var=False, nan_policy="raise")
    pvalues = np.asarray(test.pvalue, dtype=float)
    pvalues = np.where(np.isfinite(pvalues), pvalues, 1.0)
    adjusted = _bh_adjust(pvalues)
    order = np.lexsort((np.asarray(features), adjusted, -np.abs(estimate)))
    results = [
        {
            "feature_id": features[index],
            "contrast": f"{group_names[1]} - {group_names[0]}",
            "log2_cpm_difference": round(float(estimate[index]), 10),
            "p_value": round(float(pvalues[index]), 12),
            "q_value_bh": round(float(adjusted[index]), 12),
        }
        for index in order
    ]
    return {
        "schema_version": "1.0",
        "analysis_class": "exploratory_screen",
        "executor": {"name": "omics_screen", "version": VERSION},
        "sources": {
            "matrix": {"path": str(matrix_path), "sha256": _sha256(matrix_path)},
            "samples": {"path": str(samples_path), "sha256": _sha256(samples_path)},
        },
        "design": {"groups": group_names, "sample_count": len(sample_ids), "sample_ids": sample_ids},
        "qc": {
            "input_features": int(len(matrix)),
            "retained_features": int(keep.sum()),
            "filter": {"min_total": min_total, "min_samples": min_samples},
            "library_sizes": {sample_ids[i]: int(library_sizes[i]) for i in range(len(sample_ids))},
            "warnings": warnings,
        },
        "pca": {
            "transformation": "log2(CPM + 1)",
            "variance_ratio": [round(float(value), 10) for value in variance_ratio],
            "scores": [
                {"sample_id": sample_ids[i], **{f"PC{j + 1}": round(float(scores[i, j]), 10) for j in range(components)}}
                for i in range(len(sample_ids))
            ],
        },
        "differential_screen": {
            "method": "Welch t-test on log2(CPM + 1); exploratory only",
            "multiplicity": "Benjamini-Hochberg across retained features",
            "results": results,
        },
        "confirmatory_status": "not_confirmatory",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-total", type=int, default=10)
    parser.add_argument("--min-samples", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        result = execute(Path(args.matrix), Path(args.samples), min_total=args.min_total, min_samples=args.min_samples)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, OmicsInputError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
