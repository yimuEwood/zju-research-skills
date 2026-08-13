#!/usr/bin/env python3
"""Execute a small, design-bound set of confirmatory analyses offline.

The executor intentionally supports a narrow core that can be checked
numerically: Welch and paired t tests, Pearson correlation, and simple OLS.
Unsupported models fail explicitly instead of being approximated silently.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import re
from pathlib import Path
from typing import Any, Iterable

try:
    import numpy as np
    import scipy
    from scipy import stats
except ImportError as exc:  # pragma: no cover - exercised only in incomplete runtimes
    raise SystemExit(
        "execute_analysis.py requires numpy and scipy; install them in the active Python environment"
    ) from exc


SUPPORTED_METHODS = {
    "welch_ttest",
    "paired_ttest",
    "pearson_correlation",
    "simple_ols",
}
MISSING = {"", "na", "n/a", "nan", "none", "null", "."}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]
    if suffix in {".json", ".jsonl"}:
        text = path.read_text(encoding="utf-8-sig")
        if suffix == ".jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            payload = json.loads(text)
            rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("JSON input must be a list of row objects or an object with a rows list")
        return [dict(row) for row in rows]
    if suffix == ".xlsx":
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - depends on optional runtime
            raise ValueError("XLSX input requires pandas and openpyxl") from exc
        return pd.read_excel(path).replace({np.nan: None}).to_dict(orient="records")
    raise ValueError("Supported data formats are CSV, TSV, JSON, JSONL, and XLSX")


def numeric(value: Any, column: str, row_number: int) -> float | None:
    if value is None or str(value).strip().casefold() in MISSING:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"row {row_number}: {column} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"row {row_number}: {column} must be finite")
    return number


def require_columns(rows: list[dict[str, Any]], columns: Iterable[str]) -> None:
    if not rows:
        raise ValueError("data table is empty")
    available = set().union(*(row.keys() for row in rows))
    missing = [column for column in columns if not column or column not in available]
    if missing:
        raise ValueError(f"data table is missing required columns: {sorted(set(missing))}")


def result_id_for(analysis: dict[str, Any], execution: dict[str, Any]) -> str:
    supplied = str(execution.get("result_id") or "").strip()
    if supplied:
        return supplied
    safe = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(analysis["analysis_id"])).strip("-")
    return f"RES-{safe}"


def _direction(estimate: float, positive_label: str) -> str:
    if estimate > 0:
        return f"higher_{positive_label}"
    if estimate < 0:
        return f"lower_{positive_label}"
    return "no_numeric_direction"


def _shapiro(values: np.ndarray, label: str) -> dict[str, Any]:
    if len(values) < 3:
        return {"check": f"shapiro_{label}", "result": "not_assessed", "reason": "fewer_than_3_values"}
    if len(values) > 5000:
        values = values[:5000]
        scope = "first_5000_sorted_values"
    else:
        scope = "all_values"
    if np.ptp(values) == 0:
        return {"check": f"shapiro_{label}", "result": "not_assessed", "reason": "constant_values"}
    statistic, p_value = stats.shapiro(values)
    return {
        "check": f"shapiro_{label}",
        "result": "possible_non_normality" if p_value < 0.05 else "no_strong_departure_detected",
        "statistic": float(statistic),
        "p_value": float(p_value),
        "scope": scope,
        "consequence": "inspect distribution and sensitivity analysis; do not treat this test as an automatic model selector",
    }


def _ci(estimate: float, standard_error: float, df: float, level: float) -> dict[str, float]:
    critical = float(stats.t.ppf(0.5 + level / 2.0, df))
    return {
        "level": level,
        "lower": estimate - critical * standard_error,
        "upper": estimate + critical * standard_error,
    }


def _unit_counts(
    rows: list[dict[str, Any]],
    unit_column: str,
    selected_indices: list[int],
    *,
    paired: bool = False,
) -> tuple[int, int]:
    identifiers = [str(rows[index].get(unit_column, "")).strip() for index in selected_indices]
    if any(not identifier for identifier in identifiers):
        raise ValueError(f"{unit_column} contains missing experimental-unit identifiers")
    unique = set(identifiers)
    if not paired and len(unique) != len(identifiers):
        raise ValueError(
            "multiple observations were found for an experimental unit; aggregate technical replicates "
            "according to the frozen contract before execution"
        )
    return len(unique), len(identifiers)


def execute_welch(
    rows: list[dict[str, Any]], analysis: dict[str, Any], execution: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    value_column = str(execution.get("value_column") or "")
    group_column = str(execution.get("group_column") or "")
    unit_column = str(execution.get("experimental_unit_column") or "")
    reference = str(execution.get("reference_group") or "")
    comparison = str(execution.get("comparison_group") or "")
    require_columns(rows, [value_column, group_column, unit_column])
    if not reference or not comparison or reference == comparison:
        raise ValueError("Welch t test requires distinct reference_group and comparison_group")

    values: dict[str, list[tuple[int, float]]] = {reference: [], comparison: []}
    excluded: list[int] = []
    for index, row in enumerate(rows):
        group = str(row.get(group_column, "")).strip()
        if group not in values:
            continue
        number = numeric(row.get(value_column), value_column, index + 2)
        if number is None:
            excluded.append(index + 2)
            continue
        values[group].append((index, number))
    if min(map(len, values.values())) < 2:
        raise ValueError("Welch t test requires at least two complete experimental units per group")
    selected = [index for pairs in values.values() for index, _ in pairs]
    unit_count, observation_count = _unit_counts(rows, unit_column, selected)
    ref = np.asarray(sorted(number for _, number in values[reference]), dtype=float)
    comp = np.asarray(sorted(number for _, number in values[comparison]), dtype=float)
    mean_ref = float(np.mean(ref))
    mean_comp = float(np.mean(comp))
    estimate = mean_comp - mean_ref
    var_ref = float(np.var(ref, ddof=1))
    var_comp = float(np.var(comp, ddof=1))
    se2_ref = var_ref / len(ref)
    se2_comp = var_comp / len(comp)
    standard_error = math.sqrt(se2_ref + se2_comp)
    if standard_error == 0:
        raise ValueError("Welch t test is undefined because both groups have zero variance")
    df = (se2_ref + se2_comp) ** 2 / (
        (se2_ref**2) / (len(ref) - 1) + (se2_comp**2) / (len(comp) - 1)
    )
    statistic = estimate / standard_error
    p_value = float(2 * stats.t.sf(abs(statistic), df))
    level = float(execution.get("confidence_level", 0.95))
    if not 0 < level < 1:
        raise ValueError("confidence_level must lie between 0 and 1")

    pooled_denominator = len(ref) + len(comp) - 2
    pooled_sd = math.sqrt(
        ((len(ref) - 1) * var_ref + (len(comp) - 1) * var_comp) / pooled_denominator
    )
    cohen_d = estimate / pooled_sd if pooled_sd else None
    correction = 1 - 3 / (4 * (len(ref) + len(comp)) - 9)
    hedges_g = cohen_d * correction if cohen_d is not None else None
    levene_stat, levene_p = stats.levene(ref, comp, center="median")
    mw = stats.mannwhitneyu(comp, ref, alternative="two-sided")
    diagnostics = [
        _shapiro(ref, f"{reference}"),
        _shapiro(comp, f"{comparison}"),
        {
            "check": "levene_median",
            "result": "variance_difference_flag" if levene_p < 0.05 else "no_strong_variance_difference_detected",
            "statistic": float(levene_stat),
            "p_value": float(levene_p),
            "consequence": "Welch inference remains primary; inspect group distributions and influential values",
        },
    ]
    result = {
        "result_id": result_id_for(analysis, execution),
        "analysis_id": analysis["analysis_id"],
        "outcome_id": analysis["outcome_ids"][0],
        "result_kind": "inferential",
        "analysis_population": analysis["analysis_population"],
        "effect_measure": analysis["effect_measure"],
        "estimate": estimate,
        "unit": execution.get("unit") or "not_specified",
        "direction": _direction(estimate, f"in_{comparison}_versus_{reference}"),
        "ci": _ci(estimate, standard_error, df, level),
        "p_value": p_value,
        "multiplicity_status": "pending_execution_family_reconciliation",
        "n": {"experimental_units": unit_count, "observations": observation_count},
        "diagnostics": diagnostics,
        "sensitivity_analyses": [{
            "analysis_id": f"{analysis['analysis_id']}-MWU",
            "method": "mann_whitney_u_two_sided",
            "statistic": float(mw.statistic),
            "p_value": float(mw.pvalue),
        }],
        "source_anchor": "analysis-run.json#/analyses/" + str(execution["_run_index"]),
        "status": "verified",
        "method": "welch_ttest",
        "test_statistic": statistic,
        "degrees_of_freedom": df,
        "standard_error": standard_error,
        "group_summaries": {
            reference: {"n": len(ref), "mean": mean_ref, "sd": math.sqrt(var_ref)},
            comparison: {"n": len(comp), "mean": mean_comp, "sd": math.sqrt(var_comp)},
        },
        "effect_sizes": {"cohen_d": cohen_d, "hedges_g": hedges_g},
        "execution_binding": {
            "value_column": value_column,
            "group_column": group_column,
            "experimental_unit_column": unit_column,
            "reference_group": reference,
            "comparison_group": comparison,
        },
        "verification_scope": "deterministic_numeric_execution_only",
    }
    run = {
        "analysis_id": analysis["analysis_id"],
        "result_id": result["result_id"],
        "method": "welch_ttest",
        "rows_input": len(rows),
        "rows_used": observation_count,
        "excluded_source_rows": excluded,
        "columns": {
            "value": value_column,
            "group": group_column,
            "experimental_unit": unit_column,
        },
        "groups": {"reference": reference, "comparison": comparison},
    }
    return result, run


def execute_paired(
    rows: list[dict[str, Any]], analysis: dict[str, Any], execution: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    value_column = str(execution.get("value_column") or "")
    condition_column = str(execution.get("condition_column") or execution.get("group_column") or "")
    unit_column = str(execution.get("experimental_unit_column") or execution.get("pair_id_column") or "")
    reference = str(execution.get("reference_group") or "")
    comparison = str(execution.get("comparison_group") or "")
    require_columns(rows, [value_column, condition_column, unit_column])
    if not reference or not comparison or reference == comparison:
        raise ValueError("Paired t test requires distinct reference_group and comparison_group")
    pairs: dict[str, dict[str, tuple[int, float]]] = {}
    excluded: list[int] = []
    for index, row in enumerate(rows):
        condition = str(row.get(condition_column, "")).strip()
        if condition not in {reference, comparison}:
            continue
        unit = str(row.get(unit_column, "")).strip()
        if not unit:
            raise ValueError(f"row {index + 2}: missing paired experimental-unit identifier")
        number = numeric(row.get(value_column), value_column, index + 2)
        if number is None:
            excluded.append(index + 2)
            continue
        if condition in pairs.setdefault(unit, {}):
            raise ValueError(f"paired unit {unit!r} has duplicate observations for {condition!r}")
        pairs[unit][condition] = (index, number)
    complete_ids = sorted(unit for unit, values in pairs.items() if set(values) == {reference, comparison})
    incomplete = sorted(set(pairs) - set(complete_ids))
    if len(complete_ids) < 2:
        raise ValueError("Paired t test requires at least two complete pairs")
    ref = np.asarray([pairs[unit][reference][1] for unit in complete_ids], dtype=float)
    comp = np.asarray([pairs[unit][comparison][1] for unit in complete_ids], dtype=float)
    differences = comp - ref
    estimate = float(np.mean(differences))
    sd_difference = float(np.std(differences, ddof=1))
    standard_error = sd_difference / math.sqrt(len(differences))
    if standard_error == 0:
        raise ValueError("Paired t test is undefined because all pair differences are identical")
    df = float(len(differences) - 1)
    statistic = estimate / standard_error
    p_value = float(2 * stats.t.sf(abs(statistic), df))
    level = float(execution.get("confidence_level", 0.95))
    if not 0 < level < 1:
        raise ValueError("confidence_level must lie between 0 and 1")
    try:
        sensitivity = stats.wilcoxon(differences, alternative="two-sided")
        sensitivity_record: dict[str, Any] = {
            "analysis_id": f"{analysis['analysis_id']}-WILCOXON",
            "method": "wilcoxon_signed_rank_two_sided",
            "statistic": float(sensitivity.statistic),
            "p_value": float(sensitivity.pvalue),
        }
    except ValueError as exc:
        sensitivity_record = {
            "analysis_id": f"{analysis['analysis_id']}-WILCOXON",
            "method": "wilcoxon_signed_rank_two_sided",
            "status": "not_assessed",
            "reason": str(exc),
        }
    result = {
        "result_id": result_id_for(analysis, execution),
        "analysis_id": analysis["analysis_id"],
        "outcome_id": analysis["outcome_ids"][0],
        "result_kind": "inferential",
        "analysis_population": analysis["analysis_population"],
        "effect_measure": analysis["effect_measure"],
        "estimate": estimate,
        "unit": execution.get("unit") or "not_specified",
        "direction": _direction(estimate, f"in_{comparison}_versus_{reference}"),
        "ci": _ci(estimate, standard_error, df, level),
        "p_value": p_value,
        "multiplicity_status": "pending_execution_family_reconciliation",
        "n": {"experimental_units": len(complete_ids), "observations": 2 * len(complete_ids)},
        "diagnostics": [_shapiro(np.sort(differences), "paired_differences")],
        "sensitivity_analyses": [sensitivity_record],
        "source_anchor": "analysis-run.json#/analyses/" + str(execution["_run_index"]),
        "status": "verified",
        "method": "paired_ttest",
        "test_statistic": statistic,
        "degrees_of_freedom": df,
        "standard_error": standard_error,
        "pair_difference_sd": sd_difference,
        "effect_sizes": {"cohen_dz": estimate / sd_difference if sd_difference else None},
        "execution_binding": {
            "value_column": value_column,
            "condition_column": condition_column,
            "experimental_unit_column": unit_column,
            "reference_group": reference,
            "comparison_group": comparison,
        },
        "verification_scope": "deterministic_numeric_execution_only",
    }
    run = {
        "analysis_id": analysis["analysis_id"],
        "result_id": result["result_id"],
        "method": "paired_ttest",
        "rows_input": len(rows),
        "rows_used": 2 * len(complete_ids),
        "complete_pair_ids": complete_ids,
        "incomplete_pair_ids_excluded": incomplete,
        "excluded_source_rows": excluded,
        "columns": {
            "value": value_column,
            "condition": condition_column,
            "experimental_unit": unit_column,
        },
        "groups": {"reference": reference, "comparison": comparison},
    }
    return result, run


def _complete_xy(
    rows: list[dict[str, Any]], x_column: str, y_column: str, unit_column: str
) -> tuple[np.ndarray, np.ndarray, list[int], list[int]]:
    require_columns(rows, [x_column, y_column, unit_column])
    selected: list[int] = []
    excluded: list[int] = []
    x_values: list[float] = []
    y_values: list[float] = []
    for index, row in enumerate(rows):
        x = numeric(row.get(x_column), x_column, index + 2)
        y = numeric(row.get(y_column), y_column, index + 2)
        if x is None or y is None:
            excluded.append(index + 2)
            continue
        selected.append(index)
        x_values.append(x)
        y_values.append(y)
    _unit_counts(rows, unit_column, selected)
    if len(selected) < 4:
        raise ValueError("Correlation and simple OLS require at least four complete experimental units")
    x_array = np.asarray(x_values, dtype=float)
    y_array = np.asarray(y_values, dtype=float)
    if np.ptp(x_array) == 0 or np.ptp(y_array) == 0:
        raise ValueError("Correlation and simple OLS require variation in both variables")
    return x_array, y_array, selected, excluded


def execute_pearson(
    rows: list[dict[str, Any]], analysis: dict[str, Any], execution: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    x_column = str(execution.get("x_column") or "")
    y_column = str(execution.get("y_column") or "")
    unit_column = str(execution.get("experimental_unit_column") or "")
    x, y, selected, excluded = _complete_xy(rows, x_column, y_column, unit_column)
    statistic = stats.pearsonr(x, y)
    estimate = float(statistic.statistic)
    p_value = float(statistic.pvalue)
    level = float(execution.get("confidence_level", 0.95))
    if not 0 < level < 1:
        raise ValueError("confidence_level must lie between 0 and 1")
    # Fisher-z interval; clipping avoids infinities for near-perfect sampled r.
    clipped = max(-0.999999999999, min(0.999999999999, estimate))
    z = math.atanh(clipped)
    z_critical = float(stats.norm.ppf(0.5 + level / 2))
    z_se = 1 / math.sqrt(len(x) - 3)
    ci = {
        "level": level,
        "lower": math.tanh(z - z_critical * z_se),
        "upper": math.tanh(z + z_critical * z_se),
    }
    spearman = stats.spearmanr(x, y)
    result = {
        "result_id": result_id_for(analysis, execution),
        "analysis_id": analysis["analysis_id"],
        "outcome_id": analysis["outcome_ids"][0],
        "result_kind": "inferential",
        "analysis_population": analysis["analysis_population"],
        "effect_measure": analysis["effect_measure"],
        "estimate": estimate,
        "unit": execution.get("unit") or "unitless",
        "direction": "positive_association" if estimate > 0 else "negative_association" if estimate < 0 else "no_numeric_direction",
        "ci": ci,
        "p_value": p_value,
        "multiplicity_status": "pending_execution_family_reconciliation",
        "n": {"experimental_units": len(selected), "observations": len(selected)},
        "diagnostics": [
            {"check": "linearity", "result": "requires_visual_inspection", "consequence": "inspect scatter and residual structure"},
            {"check": "influential_points", "result": "requires_visual_inspection", "consequence": "inspect sensitivity to influential observations"},
        ],
        "sensitivity_analyses": [{
            "analysis_id": f"{analysis['analysis_id']}-SPEARMAN",
            "method": "spearman_rank_correlation",
            "estimate": float(spearman.statistic),
            "p_value": float(spearman.pvalue),
        }],
        "source_anchor": "analysis-run.json#/analyses/" + str(execution["_run_index"]),
        "status": "verified",
        "method": "pearson_correlation",
        "execution_binding": {
            "x_column": x_column,
            "y_column": y_column,
            "experimental_unit_column": unit_column,
        },
        "verification_scope": "deterministic_numeric_execution_only",
    }
    run = {
        "analysis_id": analysis["analysis_id"],
        "result_id": result["result_id"],
        "method": "pearson_correlation",
        "rows_input": len(rows),
        "rows_used": len(selected),
        "excluded_source_rows": excluded,
        "columns": {"x": x_column, "y": y_column, "experimental_unit": unit_column},
    }
    return result, run


def execute_ols(
    rows: list[dict[str, Any]], analysis: dict[str, Any], execution: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    x_column = str(execution.get("x_column") or "")
    y_column = str(execution.get("y_column") or "")
    unit_column = str(execution.get("experimental_unit_column") or "")
    x, y, selected, excluded = _complete_xy(rows, x_column, y_column, unit_column)
    fitted = stats.linregress(x, y)
    estimate = float(fitted.slope)
    standard_error = float(fitted.stderr)
    df = float(len(x) - 2)
    level = float(execution.get("confidence_level", 0.95))
    if not 0 < level < 1:
        raise ValueError("confidence_level must lie between 0 and 1")
    residuals = y - (float(fitted.intercept) + estimate * x)
    result = {
        "result_id": result_id_for(analysis, execution),
        "analysis_id": analysis["analysis_id"],
        "outcome_id": analysis["outcome_ids"][0],
        "result_kind": "inferential",
        "analysis_population": analysis["analysis_population"],
        "effect_measure": analysis["effect_measure"],
        "estimate": estimate,
        "unit": execution.get("unit") or "outcome_units_per_predictor_unit",
        "direction": "positive_slope" if estimate > 0 else "negative_slope" if estimate < 0 else "zero_slope",
        "ci": _ci(estimate, standard_error, df, level),
        "p_value": float(fitted.pvalue),
        "multiplicity_status": "pending_execution_family_reconciliation",
        "n": {"experimental_units": len(selected), "observations": len(selected)},
        "diagnostics": [
            _shapiro(np.sort(residuals), "ols_residuals"),
            {"check": "homoscedasticity", "result": "requires_residual_plot_inspection", "consequence": "consider robust or transformed sensitivity analysis if violated"},
            {"check": "influential_points", "result": "requires_influence_inspection", "consequence": "report sensitivity to influential observations"},
        ],
        "sensitivity_analyses": [],
        "source_anchor": "analysis-run.json#/analyses/" + str(execution["_run_index"]),
        "status": "verified",
        "method": "simple_ols",
        "intercept": float(fitted.intercept),
        "intercept_standard_error": float(fitted.intercept_stderr),
        "r_squared": float(fitted.rvalue**2),
        "standard_error": standard_error,
        "degrees_of_freedom": df,
        "execution_binding": {
            "x_column": x_column,
            "y_column": y_column,
            "experimental_unit_column": unit_column,
        },
        "verification_scope": "deterministic_numeric_execution_only",
    }
    run = {
        "analysis_id": analysis["analysis_id"],
        "result_id": result["result_id"],
        "method": "simple_ols",
        "rows_input": len(rows),
        "rows_used": len(selected),
        "excluded_source_rows": excluded,
        "columns": {"x": x_column, "y": y_column, "experimental_unit": unit_column},
    }
    return result, run


EXECUTORS = {
    "welch_ttest": execute_welch,
    "paired_ttest": execute_paired,
    "pearson_correlation": execute_pearson,
    "simple_ols": execute_ols,
}


def _holm(p_values: list[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=p_values.__getitem__)
    adjusted = [0.0] * len(p_values)
    running = 0.0
    total = len(p_values)
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (total - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=p_values.__getitem__, reverse=True)
    adjusted = [0.0] * len(p_values)
    running = 1.0
    total = len(p_values)
    for reverse_rank, index in enumerate(order):
        rank = total - reverse_rank
        running = min(running, p_values[index] * total / rank)
        adjusted[index] = min(1.0, running)
    return adjusted


def apply_multiplicity(results: list[dict[str, Any]], analyses: list[dict[str, Any]]) -> None:
    analysis_by_id = {str(item["analysis_id"]): item for item in analyses}
    families: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        analysis = analysis_by_id[result["analysis_id"]]
        family = str(analysis.get("multiplicity_family") or "unspecified")
        families.setdefault(family, []).append(result)
    for family, members in families.items():
        methods = {
            str(analysis_by_id[item["analysis_id"]].get("execution", {}).get("multiplicity_method") or "none")
            for item in members
        }
        if len(methods) != 1:
            raise ValueError(f"multiplicity family {family!r} uses inconsistent correction methods: {sorted(methods)}")
        method = methods.pop()
        if len(members) == 1:
            members[0]["raw_p_value"] = members[0]["p_value"]
            members[0]["multiplicity_status"] = f"single_test_in_family:{family}"
            continue
        if method not in {"holm", "benjamini_hochberg"}:
            raise ValueError(
                f"multiplicity family {family!r} contains {len(members)} executed tests; "
                "set execution.multiplicity_method to holm or benjamini_hochberg"
            )
        raw = [float(item["p_value"]) for item in members]
        adjusted = _holm(raw) if method == "holm" else _benjamini_hochberg(raw)
        for item, raw_p, adjusted_p in zip(members, raw, adjusted):
            item["raw_p_value"] = raw_p
            item["p_value"] = adjusted_p
            item["multiplicity_status"] = f"{method}_adjusted_within:{family};m={len(members)}"


def execute(
    data_path: Path,
    contract_path: Path,
    *,
    analysis_ids: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    data_path = data_path.resolve()
    contract_path = contract_path.resolve()
    if not data_path.is_file() or not contract_path.is_file():
        raise ValueError("data and analysis contract must both be existing local files")
    rows = load_rows(data_path)
    contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    if not isinstance(contract, dict):
        raise ValueError("analysis contract must be a JSON object")
    if contract.get("design_stage") not in {"frozen", "amended"}:
        raise ValueError("execute only a frozen or amended analysis contract")
    if not contract.get("contract_id") or not contract.get("study_id"):
        raise ValueError("analysis contract requires contract_id and study_id")
    outcome_ids = {
        str(item.get("outcome_id")) for item in contract.get("outcomes", []) if isinstance(item, dict)
    }
    selected_analyses: list[dict[str, Any]] = []
    for analysis in contract.get("analyses", []):
        if not isinstance(analysis, dict):
            continue
        analysis_id = str(analysis.get("analysis_id") or "")
        if analysis_ids is not None and analysis_id not in analysis_ids:
            continue
        execution = analysis.get("execution")
        if execution is None:
            continue
        if not isinstance(execution, dict):
            raise ValueError(f"analysis {analysis_id}: execution must be an object")
        method = str(execution.get("method") or "")
        if method not in SUPPORTED_METHODS:
            raise ValueError(
                f"analysis {analysis_id}: unsupported execution method {method!r}; "
                f"supported methods are {sorted(SUPPORTED_METHODS)}"
            )
        linked_outcomes = analysis.get("outcome_ids")
        if not isinstance(linked_outcomes, list) or len(linked_outcomes) != 1:
            raise ValueError(f"analysis {analysis_id}: this executor requires exactly one outcome_id")
        if str(linked_outcomes[0]) not in outcome_ids:
            raise ValueError(f"analysis {analysis_id}: unknown outcome ID {linked_outcomes[0]!r}")
        for required in ("analysis_population", "effect_measure", "multiplicity_family"):
            if not analysis.get(required):
                raise ValueError(f"analysis {analysis_id}: missing {required}")
        selected_analyses.append(analysis)
    if analysis_ids is not None:
        found = {str(item["analysis_id"]) for item in selected_analyses}
        if missing := analysis_ids - found:
            raise ValueError(f"requested analyses are missing or not executable: {sorted(missing)}")
    if not selected_analyses:
        raise ValueError("no analysis contains an executable execution block")

    selected_ids = {str(analysis.get("analysis_id") or "") for analysis in selected_analyses}
    execution_families = {
        str(analysis.get("multiplicity_family") or "") for analysis in selected_analyses
    }
    omitted_same_family = [
        str(analysis.get("analysis_id") or "")
        for analysis in contract.get("analyses", [])
        if isinstance(analysis, dict)
        and str(analysis.get("multiplicity_family") or "") in execution_families
        and str(analysis.get("analysis_id") or "") not in selected_ids
    ]
    if omitted_same_family:
        raise ValueError(
            "executed multiplicity families contain planned analyses without execution blocks: "
            f"{sorted(omitted_same_family)}; execute the complete family or amend the contract"
        )

    results: list[dict[str, Any]] = []
    run_entries: list[dict[str, Any]] = []
    seen_result_ids: set[str] = set()
    for run_index, analysis in enumerate(selected_analyses):
        execution = dict(analysis["execution"])
        execution["_run_index"] = run_index
        method = str(execution["method"])
        result, run_entry = EXECUTORS[method](rows, analysis, execution)
        if result["result_id"] in seen_result_ids:
            raise ValueError(f"duplicate generated result ID: {result['result_id']}")
        seen_result_ids.add(result["result_id"])
        results.append(result)
        run_entries.append(run_entry)
    apply_multiplicity(results, selected_analyses)

    model_families = {str(analysis.get("multiplicity_family") or "") for analysis in selected_analyses}
    declared_families = {
        str(item.get("family_id") or item.get("multiplicity_family") or "")
        for item in contract.get("multiplicity_families", [])
        if isinstance(item, dict)
    }
    unknown_families = model_families - declared_families if declared_families else set()
    if unknown_families:
        raise ValueError(f"analyses reference undeclared multiplicity families: {sorted(unknown_families)}")

    data_hash = sha256_file(data_path)
    contract_hash = sha256_file(contract_path)
    code_hash = sha256_file(Path(__file__).resolve())
    run_digest = hashlib.sha256(
        (data_hash + contract_hash + code_hash + stable_json([item["analysis_id"] for item in selected_analyses])).encode("utf-8")
    ).hexdigest()
    run_id = f"RUN-{run_digest[:24].upper()}"
    registry = {
        "schema_version": "1.0",
        "study_id": contract["study_id"],
        "analysis_contract_id": contract["contract_id"],
        "registry_version": run_id,
        "results": results,
        "uses": [],
        "execution_provenance": {
            "run_id": run_id,
            "data_sha256": data_hash,
            "analysis_contract_sha256": contract_hash,
            "executor_sha256": code_hash,
        },
    }
    run_manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "study_id": contract["study_id"],
        "analysis_contract_id": contract["contract_id"],
        "inputs": {
            "data": {"path": data_path.name, "sha256": data_hash, "rows": len(rows)},
            "analysis_contract": {"path": contract_path.name, "sha256": contract_hash},
            "executor": {"path": Path(__file__).name, "sha256": code_hash},
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
        "analyses": run_entries,
        "result_registry": {"path": "result-registry.json", "sha256": "pending_write"},
        "scope_note": (
            "Numeric execution and lineage were checked. Design validity, scientific interpretation, "
            "and domain adequacy still require review."
        ),
    }
    return registry, run_manifest


def write_outputs(registry: dict[str, Any], run_manifest: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_path = output_dir / "result-registry.json"
    run_path = output_dir / "analysis-run.json"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    run_manifest["result_registry"]["sha256"] = sha256_file(registry_path)
    run_path.write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"registry": registry_path, "run": run_path}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--analysis-id", action="append", dest="analysis_ids")
    args = parser.parse_args()
    try:
        registry, run_manifest = execute(
            args.data,
            args.contract,
            analysis_ids=set(args.analysis_ids) if args.analysis_ids else None,
        )
        paths = write_outputs(registry, run_manifest, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "valid": True,
        "run_id": run_manifest["run_id"],
        "analyses_executed": len(run_manifest["analyses"]),
        "result_registry": str(paths["registry"]),
        "analysis_run": str(paths["run"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
