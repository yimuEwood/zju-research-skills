#!/usr/bin/env python3
"""Validate competing hypotheses and their prospective experiment contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HYPOTHESIS_FIELDS = ("hypothesis_id", "mechanism", "assumptions", "evidence_ids", "unique_predictions", "falsifiers", "alternative_ids")
EXPERIMENT_FIELDS = ("experiment_id", "hypothesis_ids", "experimental_unit", "intervention", "control", "primary_outcome", "decision_rule")


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    hypotheses = payload.get("hypotheses", [])
    experiments = payload.get("experiments", [])
    ids = {item.get("hypothesis_id") for item in hypotheses if item.get("hypothesis_id")}
    if len(hypotheses) < 2:
        findings.append({"severity": "error", "location": "hypotheses", "message": "at least two competing hypotheses are required"})
    for index, item in enumerate(hypotheses, 1):
        for field in HYPOTHESIS_FIELDS:
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "location": f"hypotheses[{index}].{field}", "message": "required field missing"})
        unknown = set(item.get("alternative_ids", [])) - ids
        if unknown:
            findings.append({"severity": "error", "location": f"hypotheses[{index}].alternative_ids", "message": f"unknown alternatives: {sorted(unknown)}"})
    for index, item in enumerate(experiments, 1):
        for field in EXPERIMENT_FIELDS:
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "location": f"experiments[{index}].{field}", "message": "required field missing"})
        linked = set(item.get("hypothesis_ids", []))
        if len(linked) < 2 or not linked <= ids:
            findings.append({"severity": "error", "location": f"experiments[{index}].hypothesis_ids", "message": "experiment must compare at least two known hypotheses"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(hypotheses) and bool(experiments) and not errors, "findings": findings}


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