#!/usr/bin/env python3
"""Validate result, claim, figure, review, writing, and data-package ID handoffs."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def _load_registry_validator():
    path = Path(__file__).with_name("reconcile_result_registry.py")
    spec = importlib.util.spec_from_file_location("zju_reconcile_result_registry", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load registry validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


validate_registry = _load_registry_validator()


def finding(path: str, message: str) -> dict[str, str]:
    return {"path": path, "severity": "error", "message": message}


def as_rows(value: Any, path: str, findings: list[dict[str, str]]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        findings.append(finding(path, "Must be a list."))
        return []
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            findings.append(finding(f"{path}[{index}]", "Row must be an object."))
        else:
            rows.append(row)
    return rows


def index_rows(rows: list[dict[str, Any]], id_field: str, path: str, findings: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        row_id = str(row.get(id_field) or "")
        if not row_id:
            findings.append(finding(f"{path}[{index}].{id_field}", "Required ID is missing."))
        elif row_id in indexed:
            findings.append(finding(f"{path}[{index}].{id_field}", f"Duplicate ID: {row_id}"))
        else:
            indexed[row_id] = row
    return indexed


def check_links(value: Any, known: set[str], path: str, findings: list[dict[str, str]], required: bool = False) -> set[str]:
    if not isinstance(value, list):
        if required:
            findings.append(finding(path, "Required ID list is missing."))
        return set()
    links = set(map(str, value))
    if required and not links:
        findings.append(finding(path, "At least one ID is required."))
    unknown = sorted(links - known)
    if unknown:
        findings.append(finding(path, f"Unknown IDs: {unknown}"))
    return links


def evidence_index(payload: dict[str, Any]) -> set[str]:
    known = set(map(str, payload.get("evidence_ids", []))) if isinstance(payload.get("evidence_ids"), list) else set()
    for row in payload.get("evidence", []) if isinstance(payload.get("evidence"), list) else []:
        if not isinstance(row, dict):
            continue
        for field in ("evidence_id", "artifact_id", "source_id"):
            if row.get(field) not in (None, ""):
                known.add(str(row[field]))
    return known


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    contract = payload.get("analysis_contract")
    registry = payload.get("result_registry")
    if not isinstance(registry, dict):
        return {"valid": False, "findings": [finding("result_registry", "Result registry object is required.")]}
    registry_report = validate_registry(registry, analysis_contract=contract if isinstance(contract, dict) else None)
    for item in registry_report.get("issues", []):
        if item.get("severity") == "error":
            findings.append(finding(f"result_registry.{item.get('path', 'unknown')}", str(item.get("message") or "Invalid registry.")))

    result_rows = [row for row in registry.get("results", []) if isinstance(row, dict)]
    results = {str(row.get("result_id")): row for row in result_rows if row.get("result_id")}
    result_ids = set(results)
    analysis_ids = {
        str(row.get("analysis_id"))
        for row in (contract.get("analyses", []) if isinstance(contract, dict) and isinstance(contract.get("analyses"), list) else [])
        if isinstance(row, dict) and row.get("analysis_id")
    }

    claims = index_rows(as_rows(payload.get("claims"), "claims", findings), "claim_id", "claims", findings)
    claim_ids = set(claims)

    figure = payload.get("figure_manifest") if isinstance(payload.get("figure_manifest"), dict) else {}
    panels = as_rows(figure.get("panels"), "figure_manifest.panels", findings) if figure else []
    panel_ids = {str(row.get("panel_id")) for row in panels if row.get("panel_id")}
    data_inventory = payload.get("data_inventory") if isinstance(payload.get("data_inventory"), dict) else {}
    artifacts = as_rows(data_inventory.get("artifacts"), "data_inventory.artifacts", findings) if data_inventory else []
    artifact_ids = {str(row.get("artifact_id")) for row in artifacts if row.get("artifact_id")}
    known_evidence = evidence_index(payload) | analysis_ids | result_ids | panel_ids | artifact_ids

    for claim_id, claim in claims.items():
        path = f"claims[{claim_id}]"
        quantitative = claim.get("claim_type") in {"author_result", "result", "author_data_result"} or claim.get("quantitative") is True
        check_links(claim.get("result_ids"), result_ids, path + ".result_ids", findings, required=quantitative)
        check_links(claim.get("evidence_ids"), known_evidence, path + ".evidence_ids", findings, required=claim.get("status") == "supported")

    writing = payload.get("writing_contract") if isinstance(payload.get("writing_contract"), dict) else {}
    for index, section in enumerate(as_rows(writing.get("results_sections"), "writing_contract.results_sections", findings) if writing else []):
        path = f"writing_contract.results_sections[{index}]"
        check_links(section.get("result_ids"), result_ids, path + ".result_ids", findings, required=True)
        if "claim_ids" in section:
            check_links(section.get("claim_ids"), claim_ids, path + ".claim_ids", findings, required=True)
        if "evidence_ids" in section:
            check_links(section.get("evidence_ids"), known_evidence, path + ".evidence_ids", findings, required=True)

    if figure and figure.get("route") == "data_figure":
        for index, panel in enumerate(panels):
            path = f"figure_manifest.panels[{index}]"
            linked_results = check_links(panel.get("result_ids"), result_ids, path + ".result_ids", findings, required=True)
            linked_analyses = check_links(panel.get("analysis_ids"), analysis_ids, path + ".analysis_ids", findings, required=True)
            for result_id in linked_results & result_ids:
                expected_analysis = str(results[result_id].get("analysis_id") or "")
                if expected_analysis and expected_analysis not in linked_analyses:
                    findings.append(finding(path + ".analysis_ids", f"Panel omits analysis {expected_analysis} used by result {result_id}."))
            if "claim_ids" in panel:
                check_links(panel.get("claim_ids"), claim_ids, path + ".claim_ids", findings, required=True)
            if "evidence_ids" in panel:
                check_links(panel.get("evidence_ids"), known_evidence, path + ".evidence_ids", findings, required=True)

    review = payload.get("review_report") if isinstance(payload.get("review_report"), dict) else {}
    for index, concern in enumerate(as_rows(review.get("concerns"), "review_report.concerns", findings) if review else []):
        path = f"review_report.concerns[{index}]"
        check_links(concern.get("claim_ids"), claim_ids, path + ".claim_ids", findings)
        check_links(concern.get("evidence_ids"), known_evidence, path + ".evidence_ids", findings)
        check_links(concern.get("result_ids"), result_ids, path + ".result_ids", findings)

    for index, artifact in enumerate(artifacts):
        path = f"data_inventory.artifacts[{index}]"
        check_links(artifact.get("supports_claims"), claim_ids, path + ".supports_claims", findings, required=True)
        check_links(artifact.get("supports_results"), result_ids, path + ".supports_results", findings)
    packages = as_rows(data_inventory.get("reproducibility_packages"), "data_inventory.reproducibility_packages", findings) if data_inventory else []
    for index, package in enumerate(packages):
        path = f"data_inventory.reproducibility_packages[{index}]"
        check_links(package.get("result_ids"), result_ids, path + ".result_ids", findings, required=True)
        check_links(package.get("artifact_ids"), artifact_ids, path + ".artifact_ids", findings, required=True)
        expected = set(map(str, package.get("expected_outputs", []))) if isinstance(package.get("expected_outputs"), list) else set()
        unknown_expected = expected - result_ids - artifact_ids
        if unknown_expected:
            findings.append(finding(path + ".expected_outputs", f"Unknown result/artifact IDs: {sorted(unknown_expected)}"))

    return {
        "valid": registry_report.get("valid") is True and not findings,
        "registry_valid": registry_report.get("valid") is True,
        "claims": len(claims),
        "results": len(results),
        "panels": len(panels),
        "data_artifacts": len(artifacts),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8-sig")))
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
