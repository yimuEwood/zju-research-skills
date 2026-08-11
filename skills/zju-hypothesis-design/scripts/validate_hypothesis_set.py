#!/usr/bin/env python3
"""Validate competing hypotheses and rank research readiness and experiment discrimination."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any


HYPOTHESIS_FIELDS = (
    "hypothesis_id", "mechanism", "assumptions", "evidence_ids",
    "unique_predictions", "falsifiers", "alternative_ids",
)
V2_HYPOTHESIS_FIELDS = ("boundary_conditions",)
EXPERIMENT_FIELDS = (
    "experiment_id", "hypothesis_ids", "experimental_unit", "intervention",
    "control", "primary_outcome", "decision_rule",
)
V2_EXPERIMENT_FIELDS = (
    "predicted_outcomes", "inconclusive_region", "feasibility",
    "cost_level", "time_level", "orthogonal_measurement",
)
EVIDENCE_ROW_ID_FIELDS = (
    "evidence_id", "record_id", "study_id", "report_id", "citation_id", "claim_id",
)


def _known_signature(value: Any) -> bool:
    return value not in (None, "", "unknown", "not_specified", "unresolved", {})


def _signature(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unit_interval(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, result))


def _level(value: Any, default: int = 5) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return min(5, max(1, result))


def evidence_ids_from_map(evidence_map: dict[str, Any]) -> set[str]:
    known: set[str] = set()
    for row in evidence_map.get("rows", []):
        if isinstance(row, dict):
            for field in EVIDENCE_ROW_ID_FIELDS:
                value = str(row.get(field) or "").strip()
                if value:
                    known.add(value)
    for claim in evidence_map.get("claims", []):
        if isinstance(claim, dict):
            value = str(claim.get("claim_id") or "").strip()
            if value:
                known.add(value)
            known.update(str(item).strip() for item in claim.get("gap_ids", []) if str(item).strip())
    for gap in evidence_map.get("gaps", []):
        if isinstance(gap, dict):
            value = str(gap.get("gap_id") or "").strip()
            if value:
                known.add(value)
    return known


def experiment_discrimination(experiment: dict[str, Any]) -> dict[str, Any]:
    hypothesis_ids = [str(item) for item in experiment.get("hypothesis_ids", [])]
    predictions = experiment.get("predicted_outcomes", {})
    predictions = predictions if isinstance(predictions, dict) else {}
    comparisons = []
    for left, right in itertools.combinations(hypothesis_ids, 2):
        left_value = predictions.get(left)
        right_value = predictions.get(right)
        specified = _known_signature(left_value) and _known_signature(right_value)
        distinguishes = specified and _signature(left_value) != _signature(right_value)
        comparisons.append({"pair": [left, right], "specified": specified, "distinguishes": distinguishes})
    coverage = sum(item["distinguishes"] for item in comparisons) / max(1, len(comparisons))
    feasibility_raw = experiment.get("feasibility", 0.0)
    if isinstance(feasibility_raw, dict):
        values = [_unit_interval(value) for value in feasibility_raw.values()]
        feasibility = sum(values) / len(values) if values else 0.0
    else:
        feasibility = _unit_interval(feasibility_raw)
    cost = _level(experiment.get("cost_level"))
    time = _level(experiment.get("time_level"))
    orthogonal = bool(experiment.get("orthogonal_measurement"))
    score = round(
        65 * coverage
        + 15 * feasibility
        + 8 * ((6 - cost) / 5)
        + 7 * ((6 - time) / 5)
        + 5 * int(orthogonal),
        2,
    )
    return {
        "experiment_id": experiment.get("experiment_id"),
        "score": score,
        "pair_discrimination_coverage": round(coverage, 4),
        "distinguished_pairs": sum(item["distinguishes"] for item in comparisons),
        "total_pairs": len(comparisons),
        "comparisons": comparisons,
        "feasibility": round(feasibility, 4),
        "cost_level": cost,
        "time_level": time,
        "orthogonal_measurement": orthogonal,
    }


def hypothesis_readiness(hypothesis: dict[str, Any], experiment_reports: list[dict[str, Any]]) -> dict[str, Any]:
    hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
    evidence = min(1.0, len(hypothesis.get("evidence_ids", [])) / 3)
    predictions = min(1.0, len(hypothesis.get("unique_predictions", [])) / 2)
    falsifiers = min(1.0, len(hypothesis.get("falsifiers", [])) / 2)
    boundaries = 1.0 if hypothesis.get("boundary_conditions") else 0.0
    linked_reports = [
        report for report in experiment_reports
        if any(hypothesis_id in item["pair"] for item in report["comparisons"])
    ]
    test_coverage = max((report["pair_discrimination_coverage"] for report in linked_reports), default=0.0)
    score = round(30 * evidence + 25 * predictions + 20 * falsifiers + 10 * boundaries + 15 * test_coverage, 2)
    return {
        "hypothesis_id": hypothesis_id,
        "research_readiness_score": score,
        "components": {
            "evidence_linkage": round(evidence, 4),
            "prediction_specificity": round(predictions, 4),
            "falsifiability": round(falsifiers, 4),
            "boundary_clarity": round(boundaries, 4),
            "discriminating_test_coverage": round(test_coverage, 4),
        },
    }


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    hypotheses = payload.get("hypotheses", [])
    experiments = payload.get("experiments", [])
    v2 = str(payload.get("schema_version") or "") == "2.0"
    known_evidence_ids: set[str] = set()
    known_source_configured = False
    if "known_evidence_ids" in payload:
        known_source_configured = True
        supplied_known = payload.get("known_evidence_ids")
        if not isinstance(supplied_known, list):
            findings.append({"severity": "error", "location": "known_evidence_ids", "message": "must be a list"})
        else:
            known_evidence_ids.update(str(item).strip() for item in supplied_known if str(item).strip())
    if "evidence_map" in payload:
        known_source_configured = True
        evidence_map = payload.get("evidence_map")
        if not isinstance(evidence_map, dict):
            findings.append({"severity": "error", "location": "evidence_map", "message": "must be an evidence-map object"})
        else:
            known_evidence_ids.update(evidence_ids_from_map(evidence_map))
    if v2 and not known_source_configured:
        findings.append({
            "severity": "error", "location": "known_evidence_ids",
            "message": "schema v2 requires known_evidence_ids or an evidence_map so evidence links can be checked",
        })
    ids = {item.get("hypothesis_id") for item in hypotheses if item.get("hypothesis_id")}
    if len(hypotheses) < 2:
        findings.append({"severity": "error", "location": "hypotheses", "message": "at least two competing hypotheses are required"})
    for index, item in enumerate(hypotheses, 1):
        for field in HYPOTHESIS_FIELDS + (V2_HYPOTHESIS_FIELDS if v2 else ()):
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "location": f"hypotheses[{index}].{field}", "message": "required field missing"})
        unknown = set(item.get("alternative_ids", [])) - ids
        if unknown:
            findings.append({"severity": "error", "location": f"hypotheses[{index}].alternative_ids", "message": f"unknown alternatives: {sorted(unknown)}"})
        if v2 and not isinstance(item.get("contradicting_evidence_ids"), list):
            findings.append({"severity": "error", "location": f"hypotheses[{index}].contradicting_evidence_ids", "message": "field must be present as a list; use an empty list when no contradicting evidence is known"})
        if known_source_configured:
            for field in ("evidence_ids", "contradicting_evidence_ids"):
                values = item.get(field, [])
                if not isinstance(values, list):
                    findings.append({"severity": "error", "location": f"hypotheses[{index}].{field}", "message": "must be a list"})
                    continue
                unknown_evidence = sorted({str(value) for value in values} - known_evidence_ids)
                if unknown_evidence:
                    findings.append({
                        "severity": "error", "location": f"hypotheses[{index}].{field}",
                        "message": f"unknown evidence IDs: {unknown_evidence}",
                    })
    experiment_reports: list[dict[str, Any]] = []
    for index, item in enumerate(experiments, 1):
        for field in EXPERIMENT_FIELDS + (V2_EXPERIMENT_FIELDS if v2 else ()):
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "location": f"experiments[{index}].{field}", "message": "required field missing"})
        linked = set(item.get("hypothesis_ids", []))
        if len(linked) < 2 or not linked <= ids:
            findings.append({"severity": "error", "location": f"experiments[{index}].hypothesis_ids", "message": "experiment must compare at least two known hypotheses"})
        if v2:
            predictions = item.get("predicted_outcomes", {})
            if not isinstance(predictions, dict) or set(predictions) != linked:
                findings.append({"severity": "error", "location": f"experiments[{index}].predicted_outcomes", "message": "prediction signatures must cover exactly the compared hypotheses"})
            elif any(not _known_signature(predictions.get(hypothesis_id)) for hypothesis_id in linked):
                findings.append({"severity": "error", "location": f"experiments[{index}].predicted_outcomes", "message": "every compared hypothesis needs a prospective outcome signature"})
        experiment_reports.append(experiment_discrimination(item))

    experiment_ranking = sorted(experiment_reports, key=lambda item: (-item["score"], str(item["experiment_id"])))
    hypothesis_ranking = sorted(
        (hypothesis_readiness(item, experiment_reports) for item in hypotheses),
        key=lambda item: (-item["research_readiness_score"], item["hypothesis_id"]),
    )
    errors = [item for item in findings if item["severity"] == "error"]
    return {
        "valid": bool(hypotheses) and bool(experiments) and not errors,
        "schema_version": "2.0" if v2 else "legacy",
        "findings": findings,
        "hypothesis_readiness_ranking": hypothesis_ranking,
        "experiment_discrimination_ranking": experiment_ranking,
        "known_evidence_count": len(known_evidence_ids),
        "ranking_note": "Scores rank research readiness and discriminatory efficiency, not probability of truth.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
