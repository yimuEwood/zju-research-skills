#!/usr/bin/env python3
"""Validate a local experiment run manifest without executing the run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


RUN_STATUS = {"planned", "running", "completed", "failed", "partial"}
DECISIONS = {"pending", "continue", "repeat", "revise", "stop"}
RUN_TYPES = {"experiment", "data_acquisition", "simulation", "analysis", "instrument_qc", "other"}
ARTIFACT_ROLES = {"raw_data", "metadata", "qc", "processed_data", "analysis_output", "result_registry", "figure_source", "table_source", "log", "other"}


def issue(path: str, message: str, severity: str = "error") -> dict[str, str]:
    return {"path": path, "severity": severity, "message": message}


def validate_artifacts(items: Any, field: str, require_one: bool) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(items, list):
        return [issue(field, "Must be a list.")]
    if require_one and not items:
        issues.append(issue(field, "At least one artifact is required."))
    for index, item in enumerate(items):
        path = f"{field}[{index}]"
        if not isinstance(item, dict):
            issues.append(issue(path, "Artifact must be an object."))
            continue
        if not item.get("path") and not item.get("identifier"):
            issues.append(issue(path, "Provide path or stable identifier."))
        if item.get("sha256") not in (None, "", "unavailable"):
            digest = str(item["sha256"])
            if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
                issues.append(issue(path + ".sha256", "SHA-256 must be 64 hexadecimal characters or unavailable."))
    return issues


def validate(data: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    for field in ("run_id", "experiment_id", "started_at", "status"):
        if not data.get(field):
            issues.append(issue(field, "Required field is missing."))
    if data.get("status") and data["status"] not in RUN_STATUS:
        issues.append(issue("status", f"Unsupported status: {data['status']}"))
    issues.extend(validate_artifacts(data.get("inputs"), "inputs", True))
    issues.extend(validate_artifacts(data.get("outputs"), "outputs", data.get("status") == "completed"))
    if not isinstance(data.get("parameters"), dict):
        issues.append(issue("parameters", "Parameters must be an object, even when empty."))
    if not isinstance(data.get("software"), list) or not data.get("software"):
        issues.append(issue("software", "Record at least one program, environment, instrument, or firmware entry."))
    gate = data.get("decision_gate")
    if not isinstance(gate, dict):
        issues.append(issue("decision_gate", "Decision gate must be an object."))
    else:
        for field in ("criterion", "decision"):
            if not gate.get(field):
                issues.append(issue(f"decision_gate.{field}", "Required field is missing."))
        decision = gate.get("decision")
        if decision and decision not in DECISIONS:
            issues.append(issue("decision_gate.decision", f"Unsupported decision: {decision}"))
        if decision and decision != "pending":
            for field in ("actor", "decided_at"):
                if not gate.get(field):
                    issues.append(issue(f"decision_gate.{field}", "A non-pending decision requires accountability."))

    if str(data.get("schema_version")) == "2.0":
        run_type = data.get("run_type")
        if run_type not in RUN_TYPES:
            issues.append(issue("run_type", "Schema 2.0 requires a supported run type."))
        design = data.get("design_snapshot")
        if not isinstance(design, dict):
            issues.append(issue("design_snapshot", "Schema 2.0 requires a design snapshot."))
        else:
            for field in ("experimental_unit", "observational_unit", "outcome_ids"):
                if design.get(field) in (None, "", []):
                    issues.append(issue(f"design_snapshot.{field}", "Required design handoff field is missing."))
        contract = data.get("analysis_contract")
        if not isinstance(contract, dict):
            issues.append(issue("analysis_contract", "Schema 2.0 requires an analysis-contract link."))
        else:
            for field in ("contract_id", "status"):
                if not contract.get(field):
                    issues.append(issue(f"analysis_contract.{field}", "Required field is missing."))
            if contract.get("status") != "not_applicable":
                for field in ("path", "sha256"):
                    if not contract.get(field):
                        issues.append(issue(f"analysis_contract.{field}", "Linked analysis contract needs a path and digest."))
            elif not contract.get("rationale"):
                issues.append(issue("analysis_contract.rationale", "Not-applicable status requires a rationale."))
        artifact_ids: set[str] = set()
        for field in ("inputs", "outputs"):
            for index, artifact in enumerate(data.get(field, [])):
                if not isinstance(artifact, dict):
                    continue
                path = f"{field}[{index}]"
                artifact_id = str(artifact.get("artifact_id") or "")
                if not artifact_id:
                    issues.append(issue(path + ".artifact_id", "Schema 2.0 requires a stable artifact ID."))
                elif artifact_id in artifact_ids:
                    issues.append(issue(path + ".artifact_id", f"Duplicate artifact ID: {artifact_id}"))
                artifact_ids.add(artifact_id)
                if artifact.get("role") not in ARTIFACT_ROLES:
                    issues.append(issue(path + ".role", "Schema 2.0 requires a supported artifact role."))
                if field == "outputs" and not isinstance(artifact.get("derived_from"), list):
                    issues.append(issue(path + ".derived_from", "Output lineage must be a list of input artifact IDs."))
        for index, artifact in enumerate(data.get("outputs", [])):
            if not isinstance(artifact, dict):
                continue
            for parent in map(str, artifact.get("derived_from", [])):
                if parent not in artifact_ids:
                    issues.append(issue(f"outputs[{index}].derived_from", f"Unknown parent artifact ID: {parent}"))

    return {"valid": not any(item["severity"] == "error" for item in issues), "schema_version": str(data.get("schema_version") or "1.0"), "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    result = validate(data)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
