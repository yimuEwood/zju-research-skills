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
    workflow_v2 = str(payload.get("workflow_version")) == "2.0"
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
            if workflow_v2:
                for field in ("concern_id", "action_id"):
                    if not item.get(field):
                        findings.append({"severity": "error", "field": f"items[{index}].{field}", "message": "workflow 2.0 requires stable concern and action IDs"})
                if not isinstance(item.get("evidence_ids"), list) or not item.get("evidence_ids"):
                    findings.append({"severity": "error", "field": f"items[{index}].evidence_ids", "message": "verified action must link evidence artifacts"})
                diff = item.get("manuscript_diff")
                if not isinstance(diff, dict) or any(diff.get(field) in (None, "") for field in ("before", "after")):
                    findings.append({"severity": "error", "field": f"items[{index}].manuscript_diff", "message": "verified action requires exact before and after manuscript text"})
                elif not isinstance(diff.get("dependent_artifacts"), list):
                    findings.append({"severity": "error", "field": f"items[{index}].manuscript_diff.dependent_artifacts", "message": "list every manuscript/package surface invalidated by the change"})
                if item.get("action_type") == "new_analysis" and (not isinstance(item.get("result_ids"), list) or not item.get("result_ids")):
                    findings.append({"severity": "error", "field": f"items[{index}].result_ids", "message": "completed new analysis requires canonical result IDs"})
                if item.get("action_type") == "new_analysis":
                    reconciliation = item.get("result_reconciliation")
                    if not isinstance(reconciliation, dict) or reconciliation.get("status") != "passed" or not reconciliation.get("registry_version"):
                        findings.append({"severity": "error", "field": f"items[{index}].result_reconciliation", "message": "completed new analysis requires a passed cross-artifact result reconciliation and registry version"})
        if item.get("status") == "disagreed_with":
            for field in ("response_text", "disagreement_rationale"):
                if not item.get(field):
                    findings.append({"severity": "error", "field": f"items[{index}].{field}", "message": "disagreement requires a response and evidence-based rationale"})
    unresolved = sum(item.get("status") not in {"verified_complete", "disagreed_with"} for item in items)
    errors = [item for item in findings if item["severity"] == "error"]
    evidence_linked = sum(bool(item.get("evidence_ids")) for item in items if isinstance(item, dict))
    return {"valid": bool(items) and not errors, "ready": bool(items) and not errors and unresolved == 0, "workflow_version": str(payload.get("workflow_version") or "1.0"), "unresolved": unresolved, "evidence_linked_items": evidence_linked, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
