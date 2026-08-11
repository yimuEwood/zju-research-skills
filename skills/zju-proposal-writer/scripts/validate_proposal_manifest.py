#!/usr/bin/env python3
"""Validate proposal objectives, work packages, evidence, and compliance readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for field in ("proposal_id", "mode", "scheme_status", "objectives", "work_packages", "evidence", "risks", "compliance"):
        if payload.get(field) in (None, "", []):
            findings.append({"severity": "error", "field": field, "message": "required field missing"})
    objectives = payload.get("objectives", [])
    objective_ids = {item.get("objective_id") for item in objectives if item.get("objective_id")}
    for index, item in enumerate(objectives, 1):
        for field in ("objective_id", "question", "success_criteria", "evidence_ids"):
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "field": f"objectives[{index}].{field}", "message": "required field missing"})
    covered: set[str] = set()
    for index, item in enumerate(payload.get("work_packages", []), 1):
        for field in ("work_package_id", "objective_ids", "methods", "outputs", "milestones", "decision_gate"):
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "field": f"work_packages[{index}].{field}", "message": "required field missing"})
        linked = set(item.get("objective_ids", []))
        if not linked <= objective_ids:
            findings.append({"severity": "error", "field": f"work_packages[{index}].objective_ids", "message": "unknown objective"})
        covered |= linked
    missing = objective_ids - covered
    if missing:
        findings.append({"severity": "error", "field": "work_packages", "message": f"objectives without work packages: {sorted(missing)}"})
    submission_ready = payload.get("submission_ready", False)
    if submission_ready and payload.get("scheme_status") != "official_verified":
        findings.append({"severity": "error", "field": "scheme_status", "message": "submission-ready package needs verified official scheme"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": not errors, "submission_ready": bool(submission_ready and not errors), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
