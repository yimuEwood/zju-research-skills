#!/usr/bin/env python3
"""Render structured experiment intake JSON as traceable Markdown/YAML."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"password", "passwd", "cookie", "token", "secret", "credential", "session"}
DESIGN_STAGES = {"prospective", "frozen", "amended", "retrospective"}
LINEAGE_ROLES = {
    "raw_data", "metadata", "qc", "processed_data", "analysis_output",
    "result_registry", "figure_source", "table_source", "other",
}
SECTIONS = [
    ("Objective", "objective"),
    ("Materials and samples", "materials_and_samples"),
    ("Procedure", "procedure"),
    ("Deviations", "deviations"),
    ("Observations", "observations"),
    ("Results", "results"),
    ("Interpretation", "interpretation"),
    ("Anomalies", "anomalies"),
    ("Next actions", "next_actions"),
]


def quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def yaml_value(value: Any) -> str:
    """JSON flow syntax is valid YAML and preserves nested types deterministically."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def find_sensitive(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).casefold() in SENSITIVE_KEYS and item not in (None, "", False):
                found.append(child)
            found.extend(find_sensitive(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_sensitive(item, f"{path}[{index}]"))
    return found


def file_entry(item: Any) -> dict[str, str]:
    raw = item if isinstance(item, dict) else {"path": str(item)}
    path = Path(str(raw.get("path", "")))
    digest = "unavailable"
    if path.is_file():
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "media_type": str(raw.get("media_type", "unknown")),
        "note": str(raw.get("note", "")),
    }


def bullets(value: Any) -> str:
    if value in (None, "", []):
        return "- unknown"
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def normalize_v2(data: dict[str, Any]) -> dict[str, Any]:
    repeats = data.get("repeats")
    if not isinstance(repeats, dict):
        repeats = {
            "biological_replicate_definition": data.get("biological_replicate_definition"),
            "technical_replicate_definition": data.get("technical_replicate_definition"),
            "technical_replicate_aggregation": data.get("technical_replicate_aggregation"),
        }
    randomization = data.get("randomization")
    if not isinstance(randomization, dict):
        randomization = {
            "assignment": data.get("assignment"),
            "randomization_unit": data.get("randomization_unit"),
            "method": data.get("randomization_method"),
        }
    hierarchy = data.get("hierarchy")
    if not isinstance(hierarchy, dict):
        hierarchy = {
            "group_structure": data.get("group_structure"),
            "blocking_or_nesting": data.get("blocking_or_nesting"),
        }
    analysis_contract = data.get("analysis_contract")
    if not isinstance(analysis_contract, dict):
        analysis_contract = {
            "contract_id": data.get("analysis_contract_id"),
            "status": data.get("analysis_contract_status"),
            "path": data.get("analysis_contract_path"),
            "sha256": data.get("analysis_contract_sha256"),
        }
    lineage = data.get("data_lineage")
    if not isinstance(lineage, list):
        lineage = data.get("artifact_lineage") if isinstance(data.get("artifact_lineage"), list) else []
    return {
        "study_id": data.get("study_id"),
        "design_stage": data.get("design_stage"),
        "experimental_unit": data.get("experimental_unit"),
        "observational_unit": data.get("observational_unit"),
        "repeats": repeats,
        "randomization": randomization,
        "hierarchy": hierarchy,
        "repeated_measures": data.get("repeated_measures"),
        "outcome_ids": data.get("outcome_ids"),
        "data_lineage": lineage,
        "analysis_contract": analysis_contract,
        "hypothesis_ids": data.get("hypothesis_ids", []),
        "intervention": data.get("intervention"),
        "control": data.get("control"),
        "primary_outcome": data.get("primary_outcome"),
        "predicted_outcomes": data.get("predicted_outcomes", {}),
        "inconclusive_region": data.get("inconclusive_region"),
    }


def validate_v2(design: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def error(field: str, message: str) -> None:
        findings.append({"severity": "error", "field": field, "message": message})

    for field in ("study_id", "design_stage", "experimental_unit", "observational_unit"):
        if design.get(field) in (None, ""):
            error(field, "schema 2.0 field is required")
    if design.get("design_stage") not in DESIGN_STAGES:
        error("design_stage", "unsupported design stage")

    repeats = design.get("repeats")
    if not isinstance(repeats, dict):
        error("repeats", "repeats must be an object")
    else:
        for field in ("biological_replicate_definition", "technical_replicate_definition", "technical_replicate_aggregation"):
            if repeats.get(field) in (None, ""):
                error(f"repeats.{field}", "replicate definition is required; use not_applicable when appropriate")

    randomization = design.get("randomization")
    if not isinstance(randomization, dict) or randomization.get("assignment") in (None, ""):
        error("randomization.assignment", "assignment/randomization status is required")
    elif randomization.get("assignment") == "randomized" and randomization.get("randomization_unit") in (None, ""):
        error("randomization.randomization_unit", "randomized design requires its randomization unit")

    hierarchy = design.get("hierarchy")
    if not isinstance(hierarchy, dict):
        error("hierarchy", "hierarchy must be an object")
    else:
        for field in ("group_structure", "blocking_or_nesting"):
            if hierarchy.get(field) in (None, ""):
                error(f"hierarchy.{field}", "hierarchy field is required; use not_applicable when appropriate")

    if not isinstance(design.get("repeated_measures"), (bool, dict)):
        error("repeated_measures", "repeated_measures must be boolean or a structured object")
    outcomes = design.get("outcome_ids")
    if not isinstance(outcomes, list) or not outcomes or any(item in (None, "") for item in outcomes):
        error("outcome_ids", "at least one stable outcome ID is required")
    elif len({str(item) for item in outcomes}) != len(outcomes):
        error("outcome_ids", "outcome IDs must be unique")

    lineage = design.get("data_lineage")
    if not isinstance(lineage, list) or not lineage:
        error("data_lineage", "at least one artifact lineage row is required")
    else:
        artifact_ids: set[str] = set()
        for index, row in enumerate(lineage, 1):
            prefix = f"data_lineage[{index}]"
            if not isinstance(row, dict):
                error(prefix, "lineage row must be an object")
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id or artifact_id in artifact_ids:
                error(f"{prefix}.artifact_id", "artifact ID is missing or duplicated")
            artifact_ids.add(artifact_id)
            if row.get("role") not in LINEAGE_ROLES:
                error(f"{prefix}.role", "unsupported artifact role")
            if not isinstance(row.get("derived_from"), list):
                error(f"{prefix}.derived_from", "derived_from must be a list; use [] for raw data")
        for index, row in enumerate(lineage, 1):
            if isinstance(row, dict):
                unknown = set(map(str, row.get("derived_from", []))) - artifact_ids
                if unknown:
                    error(f"data_lineage[{index}].derived_from", f"unknown parent artifact IDs: {sorted(unknown)}")

    contract = design.get("analysis_contract")
    if not isinstance(contract, dict):
        error("analysis_contract", "analysis_contract must be an object")
    else:
        for field in ("contract_id", "status"):
            if contract.get(field) in (None, ""):
                error(f"analysis_contract.{field}", "analysis contract link is required")
        if contract.get("status") in {"frozen", "amended"}:
            for field in ("path", "sha256"):
                if contract.get(field) in (None, ""):
                    error(f"analysis_contract.{field}", "frozen or amended contract requires path and digest")
    return findings


def render(data: dict[str, Any]) -> str:
    sensitive = find_sensitive(data)
    if sensitive:
        raise ValueError("Sensitive credential-like fields are not allowed: " + ", ".join(sensitive))
    files = [file_entry(item) for item in data.get("source_files", [])]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    schema_version = str(data.get("schema_version") or "1.0")
    design = normalize_v2(data) if schema_version == "2.0" else None
    if design is not None:
        errors = validate_v2(design)
        if errors:
            raise ValueError("Invalid schema 2.0 design handoff: " + "; ".join(f"{row['field']}: {row['message']}" for row in errors))

    lines = [
        "---",
        f"schema_version: {quote(schema_version)}",
        f"experiment_id: {quote(data.get('experiment_id', 'unknown'))}",
        f"title: {quote(data.get('title', 'Untitled experiment'))}",
        f"project: {quote(data.get('project', 'unknown'))}",
        f"operator: {quote(data.get('operator', 'unknown'))}",
        f"experiment_started_at: {quote(data.get('experiment_started_at', 'unknown'))}",
        f"record_created_at: {quote(data.get('record_created_at', now))}",
        f"status: {quote(data.get('status', 'partial'))}",
        "sample_ids:" if data.get("sample_ids") else "sample_ids: []",
    ]
    lines.extend(f"  - {quote(item)}" for item in data.get("sample_ids", []))
    lines.append("tags:" if data.get("tags") else "tags: []")
    lines.extend(f"  - {quote(item)}" for item in data.get("tags", []))
    lines.append("source_files:" if files else "source_files: []")
    for item in files:
        lines.extend([
            f"  - path: {quote(item['path'])}",
            f"    sha256: {quote(item['sha256'])}",
            f"    media_type: {quote(item['media_type'])}",
            f"    note: {quote(item['note'])}",
        ])
    if design is not None:
        for field, value in design.items():
            lines.append(f"{field}: {yaml_value(value)}")
    lines.extend(["---", "", f"# {data.get('title', 'Untitled experiment')}", ""])
    if design is not None:
        lines.extend([
            "## Design and analysis handoff",
            "",
            "```json",
            json.dumps(design, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ])
    for heading, key in SECTIONS:
        lines.extend([f"## {heading}", "", bullets(data.get(key)), ""])
    lines.extend(["## Source manifest", ""])
    if files:
        lines.extend(f"- `{item['path']}` — SHA-256: `{item['sha256']}`; type: {item['media_type']}; {item['note']}" for item in files)
    else:
        lines.append("- No source files supplied.")
    lines.extend(["", "## Amendments", "", "- None.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    output = render(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "schema_version": str(data.get("schema_version") or "1.0"), "source_files": len(data.get("source_files", []))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
