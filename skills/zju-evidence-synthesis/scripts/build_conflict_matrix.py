#!/usr/bin/env python3
"""Build outcome-level conflict and heterogeneity summaries from evidence rows."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


POSITIVE = {"benefit", "increase", "positive", "supports"}
NEGATIVE = {"harm", "decrease", "negative", "contradicts"}
NULLISH = {"null", "no_effect", "mixed", "unclear"}
MODERATOR_FIELDS = (
    "population_or_system",
    "intervention_or_exposure",
    "comparator",
    "time_point",
    "design",
    "measurement_method",
    "dose_or_intensity",
    "setting",
)


def _direction(row: dict[str, Any]) -> str:
    value = str(row.get("evidence_role") or row.get("result_direction") or "unclear").strip().lower()
    if value in POSITIVE:
        return "supports_or_positive"
    if value in NEGATIVE:
        return "contradicts_or_negative"
    if value in NULLISH:
        return "null_mixed_or_unclear"
    return "other"


def build(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        claim_or_outcome = str(row.get("claim_id") or row.get("outcome") or "UNMAPPED").strip()
        groups[claim_or_outcome].append(row)

    matrix: list[dict[str, Any]] = []
    for group_id, items in sorted(groups.items()):
        directions: dict[str, list[str]] = defaultdict(list)
        for item in items:
            study_id = str(item.get("study_id") or "UNKNOWN")
            directions[_direction(item)].append(study_id)
        candidate_moderators = []
        for field in MODERATOR_FIELDS:
            values = sorted({str(item.get(field)).strip() for item in items if item.get(field) not in (None, "", "not_reported")})
            if len(values) > 1:
                candidate_moderators.append({"field": field, "observed_values": values})
        directional_conflict = bool(directions["supports_or_positive"] and directions["contradicts_or_negative"])
        null_disagreement = bool(
            directions["null_mixed_or_unclear"]
            and (directions["supports_or_positive"] or directions["contradicts_or_negative"])
        )
        risk_strata: dict[str, list[str]] = defaultdict(list)
        directness_strata: dict[str, list[str]] = defaultdict(list)
        for item in items:
            study_id = str(item.get("study_id") or "UNKNOWN")
            risk_strata[str(item.get("risk_of_bias") or "not_assessable")].append(study_id)
            directness_strata[str(item.get("directness") or "not_assessed")].append(study_id)
        matrix.append(
            {
                "claim_or_outcome_id": group_id,
                "study_ids": sorted({str(item.get("study_id") or "UNKNOWN") for item in items}),
                "direction_groups": {key: sorted(value) for key, value in sorted(directions.items())},
                "directional_conflict": directional_conflict,
                "null_disagreement": null_disagreement,
                "candidate_moderators": candidate_moderators,
                "risk_of_bias_strata": {key: sorted(value) for key, value in sorted(risk_strata.items())},
                "directness_strata": {key: sorted(value) for key, value in sorted(directness_strata.items())},
                "resolution_state": (
                    "context_hypothesis_available"
                    if (directional_conflict or null_disagreement) and candidate_moderators
                    else "unexplained_conflict"
                    if directional_conflict or null_disagreement
                    else "no_directional_conflict_detected"
                ),
            }
        )
    return {
        "groups": matrix,
        "summary": {
            "groups": len(matrix),
            "directional_conflicts": sum(item["directional_conflict"] for item in matrix),
            "null_disagreements": sum(item["null_disagreement"] for item in matrix),
            "unexplained_conflicts": sum(item["resolution_state"] == "unexplained_conflict" for item in matrix),
        },
        "interpretation_note": "Candidate moderators explain where studies differ; they do not prove the cause of heterogeneity.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    rows = payload if isinstance(payload, list) else payload.get("rows", [])
    result = build(rows)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
