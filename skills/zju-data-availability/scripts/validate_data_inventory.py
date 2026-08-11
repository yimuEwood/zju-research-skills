#!/usr/bin/env python3
"""Validate dataset-level availability and identifier readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROUTES = {"public_repository", "discipline_repository", "controlled_access", "within_article", "reused_public", "third_party_restricted", "justified_request", "not_applicable"}
ARTIFACT_CLASSES = {"raw_data", "metadata", "qc", "processed_data", "analysis_contract", "analysis_code", "environment", "analysis_output", "result_registry", "figure_source", "table_source", "supplement", "other"}


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    workflow_v2 = str(payload.get("workflow_version")) == "2.0"
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        return {"valid": False, "workflow_version": str(payload.get("workflow_version") or "1.0"), "artifacts": 0, "supported_result_ids": [], "findings": [{"severity": "error", "field": "artifacts", "message": "artifacts must be a list"}]}
    ids: set[str] = set()
    for index, item in enumerate(artifacts, 1):
        if not isinstance(item, dict):
            findings.append({"severity": "error", "field": f"artifacts[{index}]", "message": "artifact must be an object"})
            continue
        artifact_id = str(item.get("artifact_id", ""))
        if not artifact_id or artifact_id in ids:
            findings.append({"severity": "error", "field": f"artifacts[{index}].artifact_id", "message": "missing or duplicate artifact ID"})
        ids.add(artifact_id)
        for field in ("description", "supports_claims", "controller", "access_route", "status"):
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "field": f"artifacts[{index}].{field}", "message": "required field missing"})
        route = item.get("access_route")
        if route not in ROUTES:
            findings.append({"severity": "error", "field": f"artifacts[{index}].access_route", "message": "invalid access route"})
        if item.get("status") == "ready" and route in {"public_repository", "discipline_repository", "reused_public"}:
            if not item.get("repository") or not item.get("identifier"):
                findings.append({"severity": "error", "field": f"artifacts[{index}]", "message": "ready public artifact lacks repository or identifier"})
        if route in {"controlled_access", "third_party_restricted", "justified_request"} and not item.get("restriction_basis"):
            findings.append({"severity": "error", "field": f"artifacts[{index}].restriction_basis", "message": "restricted route lacks basis"})
        if workflow_v2:
            if item.get("artifact_class") not in ARTIFACT_CLASSES:
                findings.append({"severity": "error", "field": f"artifacts[{index}].artifact_class", "message": "workflow 2.0 requires a supported artifact class"})
            for field in ("version", "format"):
                if item.get(field) in (None, ""):
                    findings.append({"severity": "error", "field": f"artifacts[{index}].{field}", "message": "workflow 2.0 field missing"})
            if not isinstance(item.get("supports_results", []), list):
                findings.append({"severity": "error", "field": f"artifacts[{index}].supports_results", "message": "supports_results must be a list"})
            if not isinstance(item.get("derived_from", []), list):
                findings.append({"severity": "error", "field": f"artifacts[{index}].derived_from", "message": "derived_from must be a list"})
    if workflow_v2:
        for index, item in enumerate(artifacts, 1):
            if not isinstance(item, dict):
                continue
            for parent in map(str, item.get("derived_from", [])):
                if parent not in ids:
                    findings.append({"severity": "error", "field": f"artifacts[{index}].derived_from", "message": f"unknown parent artifact ID: {parent}"})
        packages = payload.get("reproducibility_packages", [])
        if not isinstance(packages, list):
            findings.append({"severity": "error", "field": "reproducibility_packages", "message": "must be a list"})
            packages = []
        if payload.get("computational_results_present") is True and not packages:
            findings.append({"severity": "error", "field": "reproducibility_packages", "message": "computational results require at least one reproducibility package"})
        for index, package in enumerate(packages, 1):
            path = f"reproducibility_packages[{index}]"
            if not isinstance(package, dict):
                findings.append({"severity": "error", "field": path, "message": "package must be an object"})
                continue
            for field in ("package_id", "result_ids", "artifact_ids", "entrypoint", "expected_outputs"):
                if package.get(field) in (None, "", []):
                    findings.append({"severity": "error", "field": f"{path}.{field}", "message": "required reproducibility-package field missing"})
            for artifact_id in map(str, package.get("artifact_ids", [])):
                if artifact_id not in ids:
                    findings.append({"severity": "error", "field": f"{path}.artifact_ids", "message": f"unknown artifact ID: {artifact_id}"})
            environment_id = str(package.get("environment_artifact_id") or "")
            if not environment_id:
                findings.append({"severity": "error", "field": f"{path}.environment_artifact_id", "message": "reproducibility package requires an environment artifact"})
            elif environment_id not in ids:
                findings.append({"severity": "error", "field": f"{path}.environment_artifact_id", "message": f"unknown artifact ID: {environment_id}"})
            verification = package.get("verification")
            if not isinstance(verification, dict) or verification.get("status") not in {"not_run", "passed", "failed", "partial"}:
                findings.append({"severity": "error", "field": f"{path}.verification", "message": "record whether the reproducibility package was run and its outcome"})
    errors = [item for item in findings if item["severity"] == "error"]
    supported_results = {str(result_id) for item in artifacts if isinstance(item, dict) for result_id in item.get("supports_results", [])}
    if workflow_v2:
        for index, package in enumerate(payload.get("reproducibility_packages", []), 1):
            if not isinstance(package, dict):
                continue
            for result_id in map(str, package.get("result_ids", [])):
                if result_id not in supported_results:
                    findings.append({"severity": "error", "field": f"reproducibility_packages[{index}].result_ids", "message": f"result ID has no supporting inventory artifact: {result_id}"})
        errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(artifacts) and not errors, "workflow_version": str(payload.get("workflow_version") or "1.0"), "artifacts": len(artifacts), "supported_result_ids": sorted(supported_results), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
