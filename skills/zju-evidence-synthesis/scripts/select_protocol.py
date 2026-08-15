#!/usr/bin/env python3
"""Select and verify an evidence-synthesis protocol from explicit decision signals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ORACLE_ID = "synthesis_protocol_decision_table_v1"
OBJECTIVES = {"quantitative_pooling", "focused_answer", "evidence_mapping", "time_bound_decision", "conceptual_integration"}


def select(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    objective = payload.get("objective")
    if objective not in OBJECTIVES:
        findings.append({"field": "objective", "message": f"objective must be one of {sorted(OBJECTIVES)}"})
    comprehensive = payload.get("comprehensive_search_required")
    if not isinstance(comprehensive, bool):
        findings.append({"field": "comprehensive_search_required", "message": "explicit boolean required"})
    deadline = payload.get("deadline_days")
    if deadline is not None and (not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= 0):
        findings.append({"field": "deadline_days", "message": "deadline_days must be a positive integer or null"})
    effects = payload.get("compatible_effect_estimates_available")

    protocol: str | None = None
    rationale: list[str] = []
    if not findings:
        if objective == "quantitative_pooling":
            if effects is not True:
                findings.append({"field": "compatible_effect_estimates_available", "message": "meta-analysis cannot be selected without compatible effect estimates"})
            elif comprehensive is not True:
                findings.append({"field": "comprehensive_search_required", "message": "quantitative pooling requires a comprehensive study set"})
            else:
                protocol, rationale = "meta_analysis", ["quantitative objective", "compatible effects", "comprehensive study set"]
        elif objective == "evidence_mapping":
            protocol, rationale = "scoping_review", ["mapping objective"]
        elif objective == "time_bound_decision":
            if deadline is None or deadline > 30:
                findings.append({"field": "deadline_days", "message": "rapid review requires a declared deadline of at most 30 days"})
            elif comprehensive is True:
                findings.append({"field": "comprehensive_search_required", "message": "rapid and exhaustive requirements conflict; revise scope or deadline"})
            else:
                protocol, rationale = "rapid_review", ["decision deadline <= 30 days", "bounded search accepted"]
        elif objective == "focused_answer":
            if comprehensive is not True:
                findings.append({"field": "comprehensive_search_required", "message": "focused systematic answer requires comprehensive search"})
            else:
                protocol, rationale = "systematic_review", ["focused answer", "comprehensive search"]
        elif objective == "conceptual_integration":
            if comprehensive is True:
                findings.append({"field": "comprehensive_search_required", "message": "conceptual narrative and exhaustive search signals conflict"})
            else:
                protocol, rationale = "narrative_synthesis", ["conceptual integration", "non-exhaustive scope"]

    declared = payload.get("declared_protocol")
    if declared is not None and protocol is not None and declared != protocol:
        findings.append({"field": "declared_protocol", "message": f"declared {declared!r}, decision table selected {protocol!r}"})
    return {"oracle_id": ORACLE_ID, "valid": protocol is not None and not findings, "selected_protocol": protocol, "rationale": rationale, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = select(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
