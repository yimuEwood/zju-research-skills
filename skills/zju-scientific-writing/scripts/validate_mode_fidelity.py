#!/usr/bin/env python3
"""Validate factual invariants for explicit scientific writing modes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ORACLE_ID = "writing_mode_contract_v1"
MODES = {"draft", "polish"}
FACT_FIELDS = ("subject", "predicate", "object", "value", "unit", "direction", "uncertainty", "citation_ids")


def _canonical(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    if isinstance(value, list):
        return sorted(_canonical(item) for item in value)
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    return value


def _signature(assertion: dict[str, Any]) -> dict[str, Any]:
    return {field: _canonical(assertion.get(field)) for field in FACT_FIELDS if field in assertion}


def _index(rows: Any, field: str, findings: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        findings.append({"field": field, "message": f"{field} must be a list"})
        return indexed
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            findings.append({"field": f"{field}[{position}]", "message": "assertion must be an object"})
            continue
        assertion_id = str(row.get("assertion_id") or "")
        if not assertion_id or assertion_id in indexed:
            findings.append({"field": f"{field}[{position}].assertion_id", "message": "assertion_id must be unique and non-empty"})
            continue
        indexed[assertion_id] = row
    return indexed


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    mode = payload.get("mode")
    if mode not in MODES:
        findings.append({"field": "mode", "message": "mode must be draft or polish"})
    if payload.get("declared_mode") is not None and payload.get("declared_mode") != mode:
        findings.append({"field": "declared_mode", "message": "declared mode differs from executed mode"})
    output = _index(payload.get("output_assertions"), "output_assertions", findings)
    if not output:
        findings.append({"field": "output_assertions", "message": "at least one output assertion is required"})

    preserved = 0
    if mode == "polish":
        source = _index(payload.get("source_assertions"), "source_assertions", findings)
        if set(source) != set(output):
            findings.append({"field": "output_assertions", "message": "polish mode must preserve the exact assertion-ID set"})
        for assertion_id in sorted(set(source) & set(output)):
            if _signature(source[assertion_id]) != _signature(output[assertion_id]):
                findings.append({"field": f"output_assertions[{assertion_id}]", "message": "polish mode changed a factual invariant"})
            else:
                preserved += 1
    elif mode == "draft":
        evidence = payload.get("evidence_ledger")
        evidence_ids = {
            str(row.get("evidence_id"))
            for row in evidence if isinstance(row, dict) and row.get("evidence_id") and row.get("status") in {"verified", "author_data"}
        } if isinstance(evidence, list) else set()
        if not evidence_ids:
            findings.append({"field": "evidence_ledger", "message": "draft mode requires verified or author-data evidence"})
        for assertion_id, row in output.items():
            links = row.get("evidence_ids")
            if not isinstance(links, list) or not links:
                findings.append({"field": f"output_assertions[{assertion_id}].evidence_ids", "message": "draft assertion requires evidence IDs"})
                continue
            unknown = sorted({str(item) for item in links} - evidence_ids)
            if unknown:
                findings.append({"field": f"output_assertions[{assertion_id}].evidence_ids", "message": f"unverified evidence IDs: {unknown}"})
        required = {str(item) for item in payload.get("required_assertion_ids", [])} if isinstance(payload.get("required_assertion_ids", []), list) else set()
        missing = sorted(required - set(output))
        if missing:
            findings.append({"field": "required_assertion_ids", "message": f"required assertions omitted: {missing}"})

    return {"oracle_id": ORACLE_ID, "valid": bool(output) and not findings, "mode": mode, "output_assertions": len(output), "preserved_assertions": preserved, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
