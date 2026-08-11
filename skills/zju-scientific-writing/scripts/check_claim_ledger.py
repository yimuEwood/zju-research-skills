#!/usr/bin/env python3
"""Validate a local claim-evidence ledger for traceability and safe drafting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ALLOWED_STATUS = {"supported", "partial", "contradicted", "unverified", "placeholder"}
LITERATURE_TYPES = {"verified_literature", "literature", "background"}
AUTHOR_RESULT_TYPES = {"author_result", "result", "author_data_result"}


def validate_item(
    item: dict[str, Any],
    index: int,
    known_result_ids: set[str] | None = None,
    require_result_ids: bool = False,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    claim_id = str(item.get("claim_id") or f"row-{index}")
    required = ("claim_id", "claim", "claim_type", "status")
    for field in required:
        if not item.get(field):
            issues.append({"claim_id": claim_id, "field": field, "severity": "error", "message": "Required field is missing."})
    status = item.get("status")
    if status and status not in ALLOWED_STATUS:
        issues.append({"claim_id": claim_id, "field": "status", "severity": "error", "message": f"Unsupported status: {status}"})
    if status == "supported" and not item.get("evidence_ids"):
        issues.append({"claim_id": claim_id, "field": "evidence_ids", "severity": "error", "message": "A supported claim must reference evidence."})
    if status == "supported" and not item.get("source_anchor"):
        issues.append({"claim_id": claim_id, "field": "source_anchor", "severity": "error", "message": "A supported claim must have a source anchor."})
    if item.get("claim_type") in LITERATURE_TYPES and item.get("citation_verified") is not True:
        issues.append({"claim_id": claim_id, "field": "citation_verified", "severity": "error", "message": "A literature claim requires an explicitly verified citation."})
    if status == "supported" and item.get("claim_type") in AUTHOR_RESULT_TYPES:
        result_ids = item.get("result_ids")
        if require_result_ids and (not isinstance(result_ids, list) or not result_ids):
            issues.append({"claim_id": claim_id, "field": "result_ids", "severity": "error", "message": "A supported author-result claim requires canonical result IDs."})
        if isinstance(result_ids, list) and known_result_ids is not None:
            for result_id in map(str, result_ids):
                if result_id not in known_result_ids:
                    issues.append({"claim_id": claim_id, "field": "result_ids", "severity": "error", "message": f"Unknown canonical result ID: {result_id}"})
    return issues


def validate(
    items: list[dict[str, Any]],
    known_result_ids: set[str] | None = None,
    require_result_ids: bool = False,
) -> dict[str, Any]:
    issues = [
        issue
        for index, item in enumerate(items, 1)
        for issue in validate_item(item, index, known_result_ids, require_result_ids)
    ]
    duplicate_ids = sorted({item.get("claim_id") for item in items if item.get("claim_id") and sum(x.get("claim_id") == item.get("claim_id") for x in items) > 1})
    issues.extend({"claim_id": value, "field": "claim_id", "severity": "error", "message": "Duplicate claim ID."} for value in duplicate_ids)
    return {"valid": not issues, "claims": len(items), "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    items = data if isinstance(data, list) else data.get("claims", [])
    known_result_ids = None
    require_result_ids = False
    if isinstance(data, dict):
        if isinstance(data.get("known_result_ids"), list):
            known_result_ids = set(map(str, data["known_result_ids"]))
        require_result_ids = str(data.get("workflow_version")) == "2.0"
    result = validate(items, known_result_ids=known_result_ids, require_result_ids=require_result_ids)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
