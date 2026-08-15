#!/usr/bin/env python3
"""Validate patent claim dependencies and source-supported feature coverage."""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any


ORACLE_ID = "patent_claim_dependency_support_v1"
SUPPORTED = {"explicit", "inherent"}


def audit(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    feature_rows = payload.get("features")
    claim_rows = payload.get("claims")
    feature_rows = feature_rows if isinstance(feature_rows, list) else []
    claim_rows = claim_rows if isinstance(claim_rows, list) else []
    features: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(feature_rows):
        prefix = f"features[{index}]"
        if not isinstance(row, dict):
            findings.append({"field": prefix, "message": "feature must be an object"})
            continue
        feature_id = str(row.get("feature_id") or "")
        if not feature_id or feature_id in features:
            findings.append({"field": f"{prefix}.feature_id", "message": "feature_id must be unique and non-empty"})
            continue
        if row.get("support_state") not in SUPPORTED:
            findings.append({"field": f"{prefix}.support_state", "message": "claim feature must be explicitly or inherently supported"})
        if not isinstance(row.get("source_anchors"), list) or not row.get("source_anchors"):
            findings.append({"field": f"{prefix}.source_anchors", "message": "claim feature requires at least one exact source anchor"})
        features[feature_id] = row
    claims: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(claim_rows):
        prefix = f"claims[{index}]"
        if not isinstance(row, dict):
            findings.append({"field": prefix, "message": "claim must be an object"})
            continue
        claim_id = str(row.get("claim_id") or "")
        if not claim_id or claim_id in claims:
            findings.append({"field": f"{prefix}.claim_id", "message": "claim_id must be unique and non-empty"})
            continue
        claims[claim_id] = row
    if not features:
        findings.append({"field": "features", "message": "at least one supported feature is required"})
    if not claims:
        findings.append({"field": "claims", "message": "at least one claim is required"})

    indegree = {claim_id: 0 for claim_id in claims}
    downstream = {claim_id: set() for claim_id in claims}
    independent = 0
    for claim_id, row in claims.items():
        role = row.get("role")
        parents = row.get("parent_claim_ids")
        linked = row.get("feature_ids")
        parents = parents if isinstance(parents, list) else []
        linked = linked if isinstance(linked, list) else []
        if role not in {"independent", "dependent"}:
            findings.append({"field": f"claims[{claim_id}].role", "message": "role must be independent or dependent"})
        if role == "independent":
            independent += 1
            if parents:
                findings.append({"field": f"claims[{claim_id}].parent_claim_ids", "message": "independent claim cannot have a parent"})
        if role == "dependent" and not parents:
            findings.append({"field": f"claims[{claim_id}].parent_claim_ids", "message": "dependent claim requires at least one parent"})
        if not linked:
            findings.append({"field": f"claims[{claim_id}].feature_ids", "message": "claim requires at least one feature"})
        unknown_features = sorted({str(item) for item in linked} - set(features))
        if unknown_features:
            findings.append({"field": f"claims[{claim_id}].feature_ids", "message": f"unknown or unsupported features: {unknown_features}"})
        for parent in {str(item) for item in parents}:
            if parent not in claims:
                findings.append({"field": f"claims[{claim_id}].parent_claim_ids", "message": f"unknown parent: {parent}"})
                continue
            downstream[parent].add(claim_id)
            indegree[claim_id] += 1
        if role == "dependent" and parents and all(str(parent) in claims for parent in parents):
            inherited = {str(feature) for parent in parents for feature in claims[str(parent)].get("feature_ids", [])}
            if {str(item) for item in linked} <= inherited:
                findings.append({"field": f"claims[{claim_id}].feature_ids", "message": "dependent claim must add a limiting feature"})
    if independent == 0:
        findings.append({"field": "claims", "message": "at least one independent claim is required"})

    queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in sorted(downstream[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    cycle = sorted(set(claims) - set(order))
    if cycle:
        findings.append({"field": "claims.parent_claim_ids", "message": f"claim dependency cycle: {cycle}"})
    return {"oracle_id": ORACLE_ID, "valid": bool(claims) and bool(features) and not findings, "claim_order": order, "dependency_cycle": cycle, "independent_claims": independent, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = audit(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
