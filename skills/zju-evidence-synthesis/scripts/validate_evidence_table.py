#!/usr/bin/env python3
"""Validate an offline study-outcome evidence table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED = (
    "study_id", "report_id", "citation_id", "design", "population_or_system",
    "sample_and_unit", "intervention_or_exposure", "comparator", "outcome",
    "time_point", "risk_of_bias", "source_anchor",
)
RISK = {"low", "some_concerns", "high", "not_assessable"}


def validate(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        key = tuple(str(row.get(field, "")) for field in ("study_id", "outcome", "time_point", "comparator"))
        if key in keys:
            findings.append({"row": index, "field": "study_id", "severity": "warning", "message": "possible duplicate study-outcome-time row"})
        keys.add(key)
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(rows) and not errors, "rows": len(rows), "errors": len(errors), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    result = validate(rows if isinstance(rows, list) else rows.get("rows", []))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
