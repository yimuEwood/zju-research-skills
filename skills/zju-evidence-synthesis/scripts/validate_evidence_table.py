#!/usr/bin/env python3
"""Validate an offline study-outcome evidence table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED = (
    "record_id", "study_id", "report_id", "citation_id", "design", "population_or_system",
    "sample_and_unit", "intervention_or_exposure", "comparator", "outcome",
    "time_point", "risk_of_bias", "source_anchor",
)
RISK = {"low", "some_concerns", "high", "not_assessable"}
EVIDENCE_ROLE = {"supports", "contradicts", "contextual", "neutral", "unclear"}
DIRECTNESS = {"direct", "indirect", "unclear", "not_assessed"}
CERTAINTY = {"very_low", "low", "moderate", "high", "not_assessed"}


def validate(payload: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    bundle_mode = isinstance(payload, dict)
    rows = payload.get("rows", []) if bundle_mode else payload
    claims = payload.get("claims", []) if bundle_mode else []
    conflicts = payload.get("conflicts", []) if bundle_mode else []
    findings: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows, 1):
        for field in REQUIRED:
            if row.get(field) in (None, ""):
                findings.append({"row": index, "field": field, "severity": "error", "message": "required field missing"})
        if row.get("risk_of_bias") not in RISK:
            findings.append({"row": index, "field": "risk_of_bias", "severity": "error", "message": "invalid risk-of-bias state"})
        if row.get("effect_estimate") not in (None, "", "not_reported") and row.get("uncertainty") in (None, "", "not_reported"):
            findings.append({"row": index, "field": "uncertainty", "severity": "error", "message": "effect estimate lacks uncertainty"})
        if bundle_mode:
            if row.get("evidence_role") not in EVIDENCE_ROLE:
                findings.append({"row": index, "field": "evidence_role", "severity": "error", "message": "invalid or missing claim relation"})
            if row.get("directness", "not_assessed") not in DIRECTNESS:
                findings.append({"row": index, "field": "directness", "severity": "error", "message": "invalid directness state"})
            if row.get("claim_id") in (None, ""):
                findings.append({"row": index, "field": "claim_id", "severity": "error", "message": "bundle rows must map to a claim"})
        key = tuple(str(row.get(field, "")) for field in ("study_id", "outcome", "time_point", "comparator"))
        if key in keys:
            findings.append({"row": index, "field": "study_id", "severity": "warning", "message": "possible duplicate study-outcome-time row"})
        keys.add(key)
    study_ids = {str(row.get("study_id")) for row in rows if row.get("study_id")}
    claim_ids = {str(row.get("claim_id")) for row in rows if row.get("claim_id")}
    roles_by_claim_study: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        claim_id = str(row.get("claim_id") or "")
        study_id = str(row.get("study_id") or "")
        role = str(row.get("evidence_role") or "")
        if claim_id and study_id and role:
            roles_by_claim_study.setdefault((claim_id, study_id), set()).add(role)
    declared_claim_ids: set[str] = set()
    for index, claim in enumerate(claims, 1):
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            findings.append({"row": f"claim[{index}]", "field": "claim_id", "severity": "error", "message": "claim_id missing"})
            continue
        if claim_id in declared_claim_ids:
            findings.append({"row": f"claim[{index}]", "field": "claim_id", "severity": "error", "message": "duplicate claim_id"})
        declared_claim_ids.add(claim_id)
        if claim.get("certainty", "not_assessed") not in CERTAINTY:
            findings.append({"row": f"claim[{index}]", "field": "certainty", "severity": "error", "message": "invalid certainty"})
        for field in ("supporting_study_ids", "contradicting_study_ids", "contextual_study_ids"):
            unknown = set(map(str, claim.get(field, []))) - study_ids
            if unknown:
                findings.append({"row": f"claim[{index}]", "field": field, "severity": "error", "message": f"unknown study IDs: {sorted(unknown)}"})
        for field, expected_role in (
            ("supporting_study_ids", "supports"),
            ("contradicting_study_ids", "contradicts"),
            ("contextual_study_ids", "contextual"),
        ):
            declared = set(map(str, claim.get(field, [])))
            actual = {
                study_id for (row_claim_id, study_id), roles in roles_by_claim_study.items()
                if row_claim_id == claim_id and expected_role in roles
            }
            wrong_role = sorted(declared - actual)
            omitted = sorted(actual - declared)
            if wrong_role:
                findings.append({
                    "row": f"claim[{index}]", "field": field, "severity": "error",
                    "message": f"declared study IDs lack a matching {expected_role} row for this claim: {wrong_role}",
                })
            if omitted:
                findings.append({
                    "row": f"claim[{index}]", "field": field, "severity": "error",
                    "message": f"{expected_role} rows omitted from the claim relation list: {omitted}",
                })
    if bundle_mode and claim_ids - declared_claim_ids:
        findings.append({"row": "claims", "field": "claim_id", "severity": "error", "message": f"row claim IDs lack claim records: {sorted(claim_ids - declared_claim_ids)}"})

    conflict_claim_ids = {str(item.get("claim_id")) for item in conflicts if item.get("claim_id")}
    direction_by_claim: dict[str, set[str]] = {}
    for row in rows:
        claim_id = str(row.get("claim_id") or "")
        if claim_id:
            direction_by_claim.setdefault(claim_id, set()).add(str(row.get("evidence_role") or "unclear"))
    uncovered = sorted(
        claim_id for claim_id, roles in direction_by_claim.items()
        if "supports" in roles and "contradicts" in roles and claim_id not in conflict_claim_ids
    )
    if uncovered:
        findings.append({"row": "conflicts", "field": "claim_id", "severity": "error", "message": f"opposing evidence lacks conflict record: {uncovered}"})

    errors = [item for item in findings if item["severity"] == "error"]
    return {
        "valid": bool(rows) and not errors,
        "bundle_mode": bundle_mode,
        "rows": len(rows),
        "claims": len(claims),
        "conflicts": len(conflicts),
        "errors": len(errors),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = validate(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
