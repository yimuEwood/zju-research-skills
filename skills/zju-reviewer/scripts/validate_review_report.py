#!/usr/bin/env python3
"""Validate concern identity, severity, and evidence anchors in a review report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if not payload.get("assessment_boundary"):
        findings.append({"severity": "error", "field": "assessment_boundary", "message": "assessment boundary required"})
    concerns = payload.get("concerns", [])
    ids: set[str] = set()
    for index, concern in enumerate(concerns, 1):
        concern_id = str(concern.get("concern_id", ""))
        if not concern_id or concern_id in ids:
            findings.append({"severity": "error", "field": f"concerns[{index}].concern_id", "message": "missing or duplicate concern ID"})
        ids.add(concern_id)
        for field in ("severity", "axis", "claim_pointer", "evidence_pointer", "concern", "why_it_matters", "resolution_test"):
            if concern.get(field) in (None, ""):
                findings.append({"severity": "error", "field": f"concerns[{index}].{field}", "message": "required field missing"})
        if concern.get("severity") not in {"major", "minor"}:
            findings.append({"severity": "error", "field": f"concerns[{index}].severity", "message": "invalid severity"})
        if concern.get("severity") == "minor" and concern.get("blocking"):
            findings.append({"severity": "error", "field": f"concerns[{index}].blocking", "message": "minor concern cannot be blocking"})
    if payload.get("mode") == "panel_review" and payload.get("mutually_blind") and not payload.get("immutable_packet_sha256"):
        findings.append({"severity": "error", "field": "immutable_packet_sha256", "message": "blind panel needs immutable packet hash"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": not errors, "concerns": len(concerns), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
