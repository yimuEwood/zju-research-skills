#!/usr/bin/env python3
"""Validate a revision-response tracker before package assembly."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


STATUSES = {"open", "in_progress", "verified_complete", "disagreed_with", "author_input_needed"}


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    items = payload.get("items", [])
    ids: set[str] = set()
    for index, item in enumerate(items, 1):
        item_id = str(item.get("comment_id", ""))
        if not item_id or item_id in ids:
            findings.append({"severity": "error", "field": f"items[{index}].comment_id", "message": "missing or duplicate comment ID"})
        ids.add(item_id)
        for field in ("source_role", "verbatim_comment", "action_type", "requested_action", "evidence_status", "status"):
            if item.get(field) in (None, ""):
                findings.append({"severity": "error", "field": f"items[{index}].{field}", "message": "required field missing"})
        if item.get("status") not in STATUSES:
            findings.append({"severity": "error", "field": f"items[{index}].status", "message": "invalid status"})
        if item.get("status") == "verified_complete":
            for field in ("response_text", "manuscript_change", "location"):
                if item.get(field) in (None, "", "LOCATION_PENDING"):
                    findings.append({"severity": "error", "field": f"items[{index}].{field}", "message": "verified item lacks response, change, or location"})
            if item.get("evidence_status") != "verified":
                findings.append({"severity": "error", "field": f"items[{index}].evidence_status", "message": "verified-complete item lacks verified evidence"})
    unresolved = sum(item.get("status") not in {"verified_complete", "disagreed_with"} for item in items)
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(items) and not errors, "ready": bool(items) and not errors and unresolved == 0, "unresolved": unresolved, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

\n