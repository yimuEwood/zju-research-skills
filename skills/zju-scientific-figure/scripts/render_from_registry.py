#!/usr/bin/env python3
"""Render a traceable one-panel scientific figure from data and canonical results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


OKABE_ITO = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00"]
SUPPORTED_PLOTS = {"group_comparison", "paired_comparison", "scatter_regression"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]
    if suffix in {".json", ".jsonl"}:
        content = path.read_text(encoding="utf-8-sig")
        if suffix == ".jsonl":
            rows = [json.loads(line) for line in content.splitlines() if line.strip()]
        else:
            payload = json.loads(content)
            rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("JSON data must be a list of row objects or an object with a rows list")
        return rows
    if suffix == ".xlsx":
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover
            raise ValueError("XLSX input requires pandas and openpyxl") from exc
        return pd.read_excel(path).to_dict(orient="records")
    raise ValueError("Supported data formats are CSV, TSV, JSON, JSONL, and XLSX")


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def select_result(registry: dict[str, Any], result_id: str) -> dict[str, Any]:
    matches = [item for item in registry.get("results", []) if item.get("result_id") == result_id]
    if len(matches) != 1:
        raise ValueError(f"result_id {result_id!r} must resolve to exactly one canonical result")
    result = matches[0]
    if result.get("status") != "verified":
        raise ValueError("only verified canonical results may be rendered")
    return result


def verify_data_binding(registry: dict[str, Any], data_path: Path) -> str:
    provenance = registry.get("execution_provenance")
    if provenance is None:
        return "registry_has_no_execution_data_hash"
    if not isinstance(provenance, dict):
        raise ValueError("registry execution_provenance must be an object when present")
    declared = str(provenance.get("data_sha256") or "").casefold()
    if not re_full_hash(declared):
        raise ValueError("registry execution_provenance.data_sha256 must be a valid SHA-256")
    actual = sha256_file(data_path)
    if declared != actual:
        raise ValueError(
            "source data SHA-256 does not match the canonical result registry; "
            "rerun the analysis or select the exact bound data file"
        )
    return "data_sha256_matches_registry"


def re_full_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def verify_rendered_counts(result: dict[str, Any], *, units: int, observations: int) -> None:
    counts = result.get("n")
    if not isinstance(counts, dict):
        raise ValueError("canonical result has no sample-count object")
    expected_units = counts.get("experimental_units")
    expected_observations = counts.get("observations")
    if expected_units != units or expected_observations != observations:
        raise ValueError(
            "rendered data counts do not match the canonical result: "
            f"rendered units={units}, observations={observations}; "
            f"registry units={expected_units}, observations={expected_observations}"
        )


def verify_spec_binding(result: dict[str, Any], spec: dict[str, Any], fields: list[str]) -> None:
    binding = result.get("execution_binding")
    if not isinstance(binding, dict):
        raise ValueError("canonical result has no execution_binding for data-column verification")
    mismatches = []
    for field in fields:
        expected = binding.get(field)
        actual = spec.get(field)
        if expected in (None, "") or actual != expected:
            mismatches.append(f"{field}: figure={actual!r}, analysis={expected!r}")
    if mismatches:
        raise ValueError("figure specification does not match the canonical analysis binding: " + "; ".join(mismatches))


def stable_jitter(identifier: str, width: float = 0.16) -> float:
    digest = hashlib.sha256(identifier.encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") / (2**32 - 1)
    return (value - 0.5) * 2 * width


def configure_matplotlib() -> Any:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover
        raise ValueError("render_from_registry.py requires matplotlib") from exc
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "axes.linewidth": 0.7,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    })
    return plt


def style_axis(ax: Any) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(width=0.7, length=3)


def group_values(rows: list[dict[str, Any]], spec: dict[str, Any]) -> tuple[list[str], dict[str, list[tuple[str, float]]]]:
    group_column = str(spec.get("group_column") or "")
    value_column = str(spec.get("value_column") or "")
    unit_column = str(spec.get("experimental_unit_column") or "")
    groups = [str(item) for item in spec.get("group_order", [])]
    if not all((group_column, value_column, unit_column)) or len(groups) < 2 or len(set(groups)) != len(groups):
        raise ValueError("group plots require group_column, value_column, experimental_unit_column, and a unique group_order")
    if not rows:
        raise ValueError("data table is empty")
    available = set().union(*(row.keys() for row in rows))
    if missing := ({group_column, value_column, unit_column} - available):
        raise ValueError(f"data table is missing required columns: {sorted(missing)}")
    values: dict[str, list[tuple[str, float]]] = {group: [] for group in groups}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        group = str(row.get(group_column, "")).strip()
        if group not in values:
            continue
        unit = str(row.get(unit_column, "")).strip()
        number = finite_number(row.get(value_column))
        if not unit or number is None:
            continue
        key = (unit, group)
        if key in seen:
            raise ValueError(f"duplicate experimental-unit observation for group plot: {key}")
        seen.add(key)
        values[group].append((unit, number))
    if any(not value for value in values.values()):
        raise ValueError("every requested group must contain at least one finite observation")
    return groups, values


def render_group_comparison(ax: Any, rows: list[dict[str, Any]], spec: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    verify_spec_binding(
        result,
        spec,
        ["value_column", "group_column", "experimental_unit_column", "reference_group", "comparison_group"],
    )
    groups, values = group_values(rows, spec)
    expected_order = [str(spec["reference_group"]), str(spec["comparison_group"])]
    if groups != expected_order:
        raise ValueError(f"group_order must be the canonical reference/comparison order: {expected_order}")
    for index, group in enumerate(groups):
        ordered = sorted(values[group])
        x = [index + stable_jitter(f"{group}:{unit}") for unit, _ in ordered]
        y = [value for _, value in ordered]
        ax.scatter(x, y, s=16, color=OKABE_ITO[index % len(OKABE_ITO)], alpha=0.72, linewidths=0, zorder=2)
        mean = sum(y) / len(y)
        if len(y) > 1:
            import scipy.stats as stats

            sd = math.sqrt(sum((item - mean) ** 2 for item in y) / (len(y) - 1))
            half = float(stats.t.ppf(0.975, len(y) - 1)) * sd / math.sqrt(len(y))
            ax.errorbar(index, mean, yerr=half, fmt="o", color="black", markersize=3.2, capsize=3, linewidth=1, zorder=3)
        else:
            ax.plot(index, mean, "o", color="black", markersize=3.2, zorder=3)
    ax.set_xticks(range(len(groups)), groups)
    ax.set_ylabel(str(spec.get("y_label") or spec["value_column"]))
    ax.set_xlabel(str(spec.get("x_label") or ""))
    observations = sum(len(value) for value in values.values())
    verify_rendered_counts(result, units=observations, observations=observations)
    return {
        "observations_rendered": observations,
        "groups": {group: len(values[group]) for group in groups},
        "uncertainty": "within-group arithmetic mean with 95% t confidence interval",
        "canonical_contrast": {
            "result_id": result["result_id"],
            "effect_measure": result.get("effect_measure"),
            "estimate": result.get("estimate"),
            "ci": result.get("ci"),
            "p_value": result.get("p_value"),
        },
    }


def render_paired_comparison(ax: Any, rows: list[dict[str, Any]], spec: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    binding = result.get("execution_binding") if isinstance(result.get("execution_binding"), dict) else {}
    condition_column = binding.get("condition_column")
    if spec.get("group_column") is None and condition_column:
        spec = {**spec, "group_column": condition_column}
    verify_spec_binding(
        result,
        {**spec, "condition_column": spec.get("group_column")},
        ["value_column", "condition_column", "experimental_unit_column", "reference_group", "comparison_group"],
    )
    groups, values = group_values(rows, spec)
    if len(groups) != 2:
        raise ValueError("paired_comparison requires exactly two groups")
    expected_order = [str(spec["reference_group"]), str(spec["comparison_group"])]
    if groups != expected_order:
        raise ValueError(f"group_order must be the canonical reference/comparison order: {expected_order}")
    by_group = {group: dict(values[group]) for group in groups}
    common = sorted(set(by_group[groups[0]]) & set(by_group[groups[1]]))
    if not common:
        raise ValueError("paired_comparison has no complete pairs")
    for unit in common:
        y = [by_group[group][unit] for group in groups]
        ax.plot([0, 1], y, color="#777777", alpha=0.45, linewidth=0.75, zorder=1)
        ax.scatter([0, 1], y, s=16, color=[OKABE_ITO[0], OKABE_ITO[1]], linewidths=0, zorder=2)
    ax.set_xticks([0, 1], groups)
    ax.set_ylabel(str(spec.get("y_label") or spec["value_column"]))
    ax.set_xlabel(str(spec.get("x_label") or ""))
    verify_rendered_counts(result, units=len(common), observations=2 * len(common))
    return {
        "complete_pairs_rendered": len(common),
        "uncertainty": "canonical paired contrast is stored in the linked result registry; individual pairs are shown",
        "canonical_contrast": {
            "result_id": result["result_id"],
            "effect_measure": result.get("effect_measure"),
            "estimate": result.get("estimate"),
            "ci": result.get("ci"),
            "p_value": result.get("p_value"),
        },
    }


def render_scatter(ax: Any, rows: list[dict[str, Any]], spec: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    verify_spec_binding(result, spec, ["x_column", "y_column", "experimental_unit_column"])
    x_column = str(spec.get("x_column") or "")
    y_column = str(spec.get("y_column") or "")
    unit_column = str(spec.get("experimental_unit_column") or "")
    if not all((x_column, y_column, unit_column)):
        raise ValueError("scatter_regression requires x_column, y_column, and experimental_unit_column")
    available = set().union(*(row.keys() for row in rows)) if rows else set()
    if missing := ({x_column, y_column, unit_column} - available):
        raise ValueError(f"data table is missing required columns: {sorted(missing)}")
    points: list[tuple[str, float, float]] = []
    seen: set[str] = set()
    for row in rows:
        unit = str(row.get(unit_column, "")).strip()
        x = finite_number(row.get(x_column))
        y = finite_number(row.get(y_column))
        if not unit or x is None or y is None:
            continue
        if unit in seen:
            raise ValueError(f"duplicate experimental-unit observation: {unit}")
        seen.add(unit)
        points.append((unit, x, y))
    if len(points) < 2:
        raise ValueError("scatter_regression requires at least two complete points")
    points.sort()
    x_values = [item[1] for item in points]
    y_values = [item[2] for item in points]
    ax.scatter(x_values, y_values, s=18, color=OKABE_ITO[0], alpha=0.72, linewidths=0)
    if result.get("method") == "simple_ols" and result.get("intercept") is not None:
        x_min, x_max = min(x_values), max(x_values)
        slope = float(result["estimate"])
        intercept = float(result["intercept"])
        ax.plot([x_min, x_max], [intercept + slope * x_min, intercept + slope * x_max], color=OKABE_ITO[1], linewidth=1.2)
    ax.set_xlabel(str(spec.get("x_label") or x_column))
    ax.set_ylabel(str(spec.get("y_label") or y_column))
    verify_rendered_counts(result, units=len(points), observations=len(points))
    return {
        "observations_rendered": len(points),
        "canonical_association": {
            "result_id": result["result_id"],
            "effect_measure": result.get("effect_measure"),
            "estimate": result.get("estimate"),
            "ci": result.get("ci"),
            "p_value": result.get("p_value"),
        },
    }


RENDERERS = {
    "group_comparison": render_group_comparison,
    "paired_comparison": render_paired_comparison,
    "scatter_regression": render_scatter,
}


def render(data_path: Path, registry_path: Path, spec_path: Path, output_dir: Path) -> tuple[dict[str, Any], Path]:
    data_path = data_path.resolve()
    registry_path = registry_path.resolve()
    spec_path = spec_path.resolve()
    for path in (data_path, registry_path, spec_path):
        if not path.is_file():
            raise ValueError(f"input file does not exist: {path}")
    rows = load_rows(data_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    if not isinstance(registry, dict) or not isinstance(spec, dict):
        raise ValueError("registry and figure spec must be JSON objects")
    for field in ("figure_id", "bounded_conclusion", "result_id", "plot_type"):
        if not spec.get(field):
            raise ValueError(f"figure spec is missing {field}")
    plot_type = str(spec["plot_type"])
    if plot_type not in SUPPORTED_PLOTS:
        raise ValueError(f"unsupported plot_type {plot_type!r}; supported values are {sorted(SUPPORTED_PLOTS)}")
    result = select_result(registry, str(spec["result_id"]))
    data_binding_status = verify_data_binding(registry, data_path)
    if spec.get("analysis_id") and spec["analysis_id"] != result.get("analysis_id"):
        raise ValueError("figure spec analysis_id does not match the canonical result")
    formats = [str(item).casefold().lstrip(".") for item in spec.get("formats", ["png", "svg", "pdf"])]
    allowed_formats = {"png", "svg", "pdf"}
    if not formats or len(set(formats)) != len(formats) or set(formats) - allowed_formats:
        raise ValueError("formats must be a unique non-empty subset of png, svg, and pdf")
    width_mm = float(spec.get("width_mm", 89.0))
    height_mm = float(spec.get("height_mm", 70.0))
    dpi = int(spec.get("dpi", 300))
    if width_mm <= 0 or height_mm <= 0 or dpi < 72:
        raise ValueError("width_mm and height_mm must be positive and dpi must be at least 72")

    plt = configure_matplotlib()
    figure, axis = plt.subplots(
        figsize=(width_mm / 25.4, height_mm / 25.4),
        constrained_layout=True,
    )
    details = RENDERERS[plot_type](axis, rows, spec, result)
    style_axis(axis)
    if spec.get("title"):
        axis.set_title(str(spec["title"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = str(spec.get("output_basename") or spec["figure_id"])
    if Path(base_name).name != base_name or base_name in {".", ".."}:
        raise ValueError("output_basename must be a simple file name without directories")
    exports: list[dict[str, Any]] = []
    for output_format in formats:
        path = output_dir / f"{base_name}.{output_format}"
        figure.savefig(path, dpi=dpi if output_format == "png" else None, bbox_inches="tight", facecolor="white")
        exports.append({
            "path": path.name,
            "format": output_format,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "dpi": dpi if output_format == "png" else "vector",
        })
    plt.close(figure)

    panel = {
        "panel_id": str(spec.get("panel_id") or "A"),
        "question": str(spec.get("question") or spec["bounded_conclusion"]),
        "source_anchor": data_path.name + str(spec.get("source_fragment") or "#all_rows"),
        "panel_type": plot_type,
        "analysis_ids": [result["analysis_id"]],
        "result_ids": [result["result_id"]],
        "experimental_unit": str(spec.get("experimental_unit") or spec.get("experimental_unit_column") or "unknown"),
        "details": details,
    }
    manifest = {
        "schema_version": "1.0",
        "figure_id": spec["figure_id"],
        "bounded_conclusion": spec["bounded_conclusion"],
        "route": "data_figure",
        "backend": "matplotlib",
        "target_width_mm": width_mm,
        "target_height_mm": height_mm,
        "source_files": [
            {"path": str(data_path), "sha256": sha256_file(data_path), "role": "source_data"},
            {"path": str(registry_path), "sha256": sha256_file(registry_path), "role": "canonical_result_registry"},
            {"path": str(spec_path), "sha256": sha256_file(spec_path), "role": "figure_spec"},
        ],
        "panels": [panel],
        "exports": exports,
        "accessibility": {
            "palette": "Okabe-Ito",
            "redundant_encoding": "individual marks and spatial grouping",
            "grayscale_check": "manual_inspection_required",
        },
        "qa_status": {
            "numeric_binding": "linked_to_verified_result_id",
            "data_binding": data_binding_status,
            "file_generation": "complete",
            "visual_inspection": "required",
            "data_to_mark_audit": "required",
        },
    }
    manifest_path = output_dir / "figure-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest, manifest_path = render(args.data, args.registry, args.spec, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "valid": True,
        "figure_id": manifest["figure_id"],
        "exports": len(manifest["exports"]),
        "manifest": str(manifest_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
