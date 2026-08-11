#!/usr/bin/env python3
"""Validate contribution, Results, and reviewer-readiness mappings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CONTRIBUTION_STATUS = {"proposed", "confirmed"}
OBJECTION_DISPOSITION = {"open", "resolved", "accepted_limitation"}


def finding(path: str, message: str, severity: str = "error") -> dict[str, str]:
    return {"path": path, "severity": severity, "message": message}


def duplicate_ids(items: list[dict[str, Any]], key: str) -> set[str]:
    values = [str(item.get(key)) for item in items if item.get(key)]
    return {value for value in values if values.count(value) > 1}


def validate(data: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    contributions = data.get("contributions", [])
    sections = data.get("results_sections", [])
    objections = data.get("reviewer_objections", [])
    if not isinstance(contributions, list) or not contributions:
        return {"valid": False, "issues": [finding("contributions", "At least one contribution is required.")]}
    if not isinstance(sections, list):
        sections = []
        issues.append(finding("results_sections", "Must be a list."))
    if not isinstance(objections, list):
        objections = []
        issues.append(finding("reviewer_objections", "Must be a list."))

    contribution_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(contributions):
        path = f"contributions[{index}]"
        if not isinstance(item, dict):
            issues.append(finding(path, "Contribution must be an object."))
            continue
        contribution_id = str(item.get("contribution_id") or "")
        if not contribution_id:
            issues.append(finding(path + ".contribution_id", "Required field is missing."))
        else:
            contribution_by_id[contribution_id] = item
        for field in ("statement", "need", "claim_boundary"):
            if not str(item.get(field) or "").strip():
                issues.append(finding(path + f".{field}", "Required field is missing."))
        evidence = item.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence:
            issues.append(finding(path + ".evidence_ids", "At least one evidence ID is required."))
        if item.get("status") not in CONTRIBUTION_STATUS:
            issues.append(finding(path + ".status", "Status must be proposed or confirmed."))
    for value in duplicate_ids([x for x in contributions if isinstance(x, dict)], "contribution_id"):
        issues.append(finding("contributions", f"Duplicate contribution ID: {value}"))

    mapped: dict[str, int] = {key: 0 for key in contribution_by_id}
    for index, item in enumerate(sections):
        path = f"results_sections[{index}]"
        if not isinstance(item, dict):
            issues.append(finding(path, "Results section must be an object."))
            continue
        if not item.get("section_id"):
            issues.append(finding(path + ".section_id", "Required field is missing."))
        contribution_ids = item.get("contribution_ids")
        evidence_ids = item.get("evidence_ids")
        if not isinstance(contribution_ids, list) or not contribution_ids:
            issues.append(finding(path + ".contribution_ids", "Map the section to at least one contribution."))
            contribution_ids = []
        if not isinstance(evidence_ids, list) or not evidence_ids:
            issues.append(finding(path + ".evidence_ids", "Map the section to observed evidence."))
            evidence_ids = []
        for contribution_id in contribution_ids:
            contribution_id = str(contribution_id)
            if contribution_id not in contribution_by_id:
                issues.append(finding(path + ".contribution_ids", f"Unknown contribution ID: {contribution_id}"))
                continue
            mapped[contribution_id] += 1
            promised = set(map(str, contribution_by_id[contribution_id].get("evidence_ids", [])))
            if promised and not promised.intersection(map(str, evidence_ids)):
                issues.append(finding(path + ".evidence_ids", f"No evidence overlaps contribution {contribution_id}."))
    for contribution_id, count in mapped.items():
        if contribution_by_id[contribution_id].get("status") == "confirmed" and count == 0:
            issues.append(finding("results_sections", f"Confirmed contribution {contribution_id} is not validated by a Results section."))

    if data.get("submission_ready") is True:
        if not objections:
            issues.append(finding("reviewer_objections", "Submission readiness requires an objection register."))
        for index, item in enumerate(objections):
            path = f"reviewer_objections[{index}]"
            if not isinstance(item, dict):
                issues.append(finding(path, "Objection must be an object."))
                continue
            for field in ("objection_id", "risk", "disposition"):
                if not item.get(field):
                    issues.append(finding(path + f".{field}", "Required field is missing."))
            disposition = item.get("disposition")
            if disposition not in OBJECTION_DISPOSITION:
                issues.append(finding(path + ".disposition", "Unsupported disposition."))
            if disposition == "open":
                issues.append(finding(path + ".disposition", "Open objection blocks submission readiness."))
            if disposition in {"resolved", "accepted_limitation"} and not item.get("response"):
                issues.append(finding(path + ".response", "Disposition requires a response or disclosure."))

    return {
        "valid": not any(item["severity"] == "error" for item in issues),
        "contributions": len(contributions),
        "results_sections": len(sections),
        "reviewer_objections": len(objections),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    result = validate(data)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
\n