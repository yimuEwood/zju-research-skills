#!/usr/bin/env python3
"""Validate one-to-one consistency between an artifact inventory and an availability statement."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ORACLE_ID = "data_statement_inventory_bijection_v1"
ACCESS = {"public", "restricted", "embargoed", "not_available"}
PERSISTENT = re.compile(r"^(?:https://(?:doi\.org/|zenodo\.org/record|figshare\.com/|osf\.io/)|(?:doi:)?10\.\d{4,9}/)", re.I)


def _index(rows: Any, field: str, findings: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        findings.append({"field": field, "message": f"{field} must be a list"})
        return indexed
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            findings.append({"field": f"{field}[{index}]", "message": "row must be an object"})
            continue
        artifact_id = str(row.get("artifact_id") or "")
        if not artifact_id or artifact_id in indexed:
            findings.append({"field": f"{field}[{index}].artifact_id", "message": "artifact_id must be unique and non-empty"})
            continue
        indexed[artifact_id] = row
    return indexed


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    inventory = _index(payload.get("inventory"), "inventory", findings)
    statement = _index(payload.get("statement_entries"), "statement_entries", findings)
    required = {artifact_id for artifact_id, row in inventory.items() if row.get("supports_claims") is not False}
    missing, extra = sorted(required - set(statement)), sorted(set(statement) - set(inventory))
    if missing:
        findings.append({"field": "statement_entries", "message": f"claim-supporting artifacts omitted: {missing}"})
    if extra:
        findings.append({"field": "statement_entries", "message": f"statement references unknown artifacts: {extra}"})
    for artifact_id in sorted(set(inventory) & set(statement)):
        source, declared = inventory[artifact_id], statement[artifact_id]
        inventory_access = source.get("access")
        if inventory_access not in ACCESS:
            findings.append({"field": f"inventory[{artifact_id}].access", "message": "invalid access state"})
        if declared.get("access") != inventory_access:
            findings.append({"field": f"statement_entries[{artifact_id}].access", "message": "statement access state differs from inventory"})
        if inventory_access == "public":
            identifier = str(declared.get("persistent_identifier") or source.get("persistent_identifier") or "")
            if not PERSISTENT.match(identifier):
                findings.append({"field": f"statement_entries[{artifact_id}].persistent_identifier", "message": "public artifact requires a recognized persistent identifier"})
            if not str(declared.get("license") or source.get("license") or "").strip():
                findings.append({"field": f"statement_entries[{artifact_id}].license", "message": "public artifact requires an explicit license"})
        elif inventory_access == "restricted":
            for field in ("restriction_reason", "request_route", "decision_authority"):
                if not str(declared.get(field) or source.get(field) or "").strip():
                    findings.append({"field": f"statement_entries[{artifact_id}].{field}", "message": "restricted access requires reason, request route, and decision authority"})
        elif inventory_access == "embargoed":
            if not str(declared.get("embargo_until") or source.get("embargo_until") or "").strip():
                findings.append({"field": f"statement_entries[{artifact_id}].embargo_until", "message": "embargoed artifact requires an embargo end date"})
        elif inventory_access == "not_available":
            if not str(declared.get("reason") or source.get("reason") or "").strip():
                findings.append({"field": f"statement_entries[{artifact_id}].reason", "message": "non-availability requires an honest reason"})
    return {"oracle_id": ORACLE_ID, "valid": bool(inventory) and not findings, "inventory_artifacts": len(inventory), "statement_artifacts": len(statement), "omitted_required": missing, "unknown_statement_artifacts": extra, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
