#!/usr/bin/env python3
"""Validate patent feature support and formal-claim eligibility."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUPPORT = {"explicit", "inherent", "needs_confirmation", "unsupported"}
FORMAL = {"independent", "dependent"}


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    features = payload.get("features", [])
    ids: set[str] = set()
    for index, feature in enumerate(features, 1):
        feature_id = str(feature.get("feature_id", ""))
        if not feature_id or feature_id in ids:
            findings.append({"severity": "error", "field": f"features[{index}].feature_id", "message": "missing or duplicate feature ID"})
        ids.add(feature_id)
        for field in ("normalized_term", "description", "support_state", "claim_role", "confidentiality"):
            if feature.get(field) in (None, ""):
                findings.append({"severity": "error", "field": f"features[{index}].{field}", "message": "required field missing"})
        state = feature.get("support_state")
        if state not in SUPPORT:
            findings.append({"severity": "error", "field": f"features[{index}].support_state", "message": "invalid support state"})
        if feature.get("claim_role") in FORMAL:
            if state not in {"explicit", "inherent"} or not feature.get("source_ids"):
                findings.append({"severity": "error", "field": f"features[{index}]", "message": "formal claim feature lacks sufficient sourced support"})
            if "TO CONFIRM" in str(feature):
                findings.append({"severity": "error", "field": f"features[{index}]", "message": "placeholder cannot appear in formal claim feature"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(features) and not errors, "features": len(features), "findings": findings}


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