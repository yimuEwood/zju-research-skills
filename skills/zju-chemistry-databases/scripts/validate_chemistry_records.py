#!/usr/bin/env python3
"""Validate identity-resolved chemistry evidence records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TYPES = {"experimental", "curated", "submitted", "computed", "predicted", "vendor", "regulatory"}


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    entities = payload.get("entities", [])
    entity_ids = {item.get("entity_id") for item in entities if item.get("entity_id")}
    if len(entity_ids) != len(entities):
        findings.append({"severity": "error", "field": "entities", "message": "entity IDs are missing or duplicated"})
    for index, entity in enumerate(entities, 1):
        if not any(entity.get(field) for field in ("inchi_key", "isomeric_smiles", "database_ids")):
            findings.append({"severity": "error", "field": f"entities[{index}]", "message": "no structure or database identity anchor"})
        if entity.get("identity_status") not in {"resolved", "provisional", "ambiguous"}:
            findings.append({"severity": "error", "field": f"entities[{index}].identity_status", "message": "invalid identity status"})
    records = payload.get("records", [])
    for index, record in enumerate(records, 1):
        for field in ("record_id", "entity_id", "property_or_endpoint", "evidence_type", "source_database", "source_anchor"):
            if record.get(field) in (None, ""):
                findings.append({"severity": "error", "field": f"records[{index}].{field}", "message": "required field missing"})
        if record.get("entity_id") not in entity_ids:
            findings.append({"severity": "error", "field": f"records[{index}].entity_id", "message": "unknown entity"})
        if record.get("evidence_type") not in TYPES:
            findings.append({"severity": "error", "field": f"records[{index}].evidence_type", "message": "invalid evidence type"})
        if record.get("value") not in (None, "") and record.get("unit") in (None, "", "not_applicable") and record.get("dimensionless") is not True:
            findings.append({"severity": "warning", "field": f"records[{index}].unit", "message": "numeric value lacks unit or explicit dimensionless flag"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(entities) and bool(records) and not errors, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
