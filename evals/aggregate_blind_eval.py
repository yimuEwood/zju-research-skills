#!/usr/bin/env python3
"""Reveal blind labels after rating and compute quality/efficiency release gates."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
RESULTS = EVALS / "results"
DIMENSION_WEIGHTS = {
    "task_completeness": 0.25,
    "evidence_traceability": 0.25,
    "scientific_validity": 0.20,
    "safety_integrity": 0.20,
    "usability": 0.10,
}
ARMS = ("no_skill", "upstream_skill", "distilled_skill")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def weighted_score(scores: dict[str, int], critical_failure: bool) -> float:
    if critical_failure:
        return 0.0
    return round(sum((scores[name] / 4) * weight * 100 for name, weight in DIMENSION_WEIGHTS.items()), 3)


def efficiency_gain(baseline: float, distilled: float) -> float:
    if baseline <= 0:
        return 0.0
    return round((baseline - distilled) / baseline * 100, 3)


def validate_ratings(case_id: str, ratings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if ratings.get("case_id") != case_id:
        raise ValueError(f"Rater case mismatch for {case_id}")
    by_label = {item["blind_label"]: item for item in ratings.get("ratings", [])}
    if set(by_label) != {"A", "B", "C"}:
        raise ValueError(f"Rater output for {case_id} must contain A, B, and C exactly once")
    for label, item in by_label.items():
        if set(item["scores"]) != set(DIMENSION_WEIGHTS):
            raise ValueError(f"Rater output for {case_id}/{label} has invalid score dimensions")
        if any(not isinstance(value, int) or value < 0 or value > 4 for value in item["scores"].values()):
            raise ValueError(f"Rater output for {case_id}/{label} has a score outside 0..4")
    return by_label


def aggregate(run_id: str, rater_ids: list[str], adjudicator_id: str | None = None) -> dict[str, Any]:
    if len(rater_ids) != 2 or len(set(rater_ids)) != 2:
        raise ValueError("Exactly two distinct rater IDs are required")
    run_dir = RESULTS / run_id
    manifest = read_json(run_dir / "run-manifest.json")
    arm_map = read_json(run_dir / "private/arm-map.json")["arm_map"]
    case_by_id = {case["id"]: case for case in read_json(EVALS / "cases.json")["cases"]}
    gates = read_json(EVALS / "quality-gates.json")["release_gate"]
    rows = []
    adjudication = []
    adjudication_resolved = []
    missing = []
    for case_id in manifest["case_ids"]:
        rater_outputs = []
        for rater_id in rater_ids:
            path = run_dir / "ratings" / rater_id / f"{case_id}.json"
            if not path.is_file():
                missing.append(f"{rater_id}:{case_id}")
                continue
            rater_outputs.append(validate_ratings(case_id, read_json(path)))
        if len(rater_outputs) != 2:
            continue
        adjudicator_output = None
        if adjudicator_id:
            adjudicator_path = run_dir / "ratings" / adjudicator_id / f"{case_id}.json"
            if adjudicator_path.is_file():
                adjudicator_output = validate_ratings(case_id, read_json(adjudicator_path))
        for label in ("A", "B", "C"):
            first, second = rater_outputs[0][label], rater_outputs[1][label]
            dimension_disagreement = {
                name: abs(first["scores"][name] - second["scores"][name])
                for name in DIMENSION_WEIGHTS
            }
            disputed = any(value > 1 for value in dimension_disagreement.values()) or first["critical_failure"] != second["critical_failure"]
            dispute = {"case_id": case_id, "blind_label": label, "dimension_disagreement": dimension_disagreement, "critical_failure_disagreement": first["critical_failure"] != second["critical_failure"]}
            if disputed and adjudicator_output is None:
                adjudication.append(dispute)
            if disputed and adjudicator_output is not None:
                third = adjudicator_output[label]
                scores = {
                    name: statistics.median([first["scores"][name], second["scores"][name], third["scores"][name]])
                    for name in DIMENSION_WEIGHTS
                }
                critical = sum(bool(item["critical_failure"]) for item in (first, second, third)) >= 2
                adjudication_resolved.append({
                    **dispute,
                    "adjudicator_id": adjudicator_id,
                    "adjudicator_score": weighted_score(third["scores"], third["critical_failure"]),
                    "method": "dimension median and critical-failure majority over three blind ratings",
                })
            else:
                scores = {
                    name: (first["scores"][name] + second["scores"][name]) / 2
                    for name in DIMENSION_WEIGHTS
                }
                critical = first["critical_failure"] or second["critical_failure"]
            quality = weighted_score(scores, critical)
            execution = read_json(run_dir / "tasks" / case_id / label / "execution.json")
            usage = execution.get("usage", {})
            rows.append({
                "case_id": case_id,
                "skill": case_by_id[case_id]["skill"],
                "blind_label": label,
                "arm": arm_map[case_id][label],
                "quality_score": quality,
                "critical_failure": critical,
                "duration_seconds": execution.get("duration_seconds", 0),
                "total_tokens": int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
                "rater_scores": {
                    rater_ids[0]: weighted_score(first["scores"], first["critical_failure"]),
                    rater_ids[1]: weighted_score(second["scores"], second["critical_failure"]),
                    **({adjudicator_id: weighted_score(adjudicator_output[label]["scores"], adjudicator_output[label]["critical_failure"])} if disputed and adjudicator_output is not None and adjudicator_id else {}),
                },
            })
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["skill"]][row["arm"]].append(row)
    skill_results = {}
    all_pass = True
    for skill in sorted(grouped):
        arms = grouped[skill]
        if any(not arms.get(arm) for arm in ARMS):
            all_pass = False
            skill_results[skill] = {"eligible": False, "reason": "missing arm results"}
            continue
        summaries = {}
        for arm in ARMS:
            summaries[arm] = {
                "cases": len(arms[arm]),
                "mean_quality": round(statistics.fmean(row["quality_score"] for row in arms[arm]), 3),
                "median_duration_seconds": round(statistics.median(row["duration_seconds"] for row in arms[arm]), 3),
                "median_total_tokens": round(statistics.median(row["total_tokens"] for row in arms[arm]), 3),
                "critical_failures": sum(row["critical_failure"] for row in arms[arm]),
            }
        stronger = max(("no_skill", "upstream_skill"), key=lambda arm: summaries[arm]["mean_quality"])
        quality_gain = round(summaries["distilled_skill"]["mean_quality"] - summaries[stronger]["mean_quality"], 3)
        duration_gain = efficiency_gain(summaries[stronger]["median_duration_seconds"], summaries["distilled_skill"]["median_duration_seconds"])
        token_gain = efficiency_gain(summaries[stronger]["median_total_tokens"], summaries["distilled_skill"]["median_total_tokens"])
        minimum_cases = read_json(EVALS / "quality-gates.json")["minimum_cases_per_skill"]
        enough_cases = all(summaries[arm]["cases"] >= minimum_cases for arm in ARMS)
        quality_path = quality_gain >= gates["minimum_primary_score_gain_points"]
        efficiency_path = (
            quality_gain >= -gates["maximum_quality_regression_points"]
            and max(duration_gain, token_gain) >= gates["minimum_efficiency_gain_percent"]
        )
        no_critical = summaries["distilled_skill"]["critical_failures"] == 0
        pending_for_skill = any(item["case_id"] in {row["case_id"] for row in arms["distilled_skill"]} for item in adjudication)
        passed = enough_cases and (quality_path or efficiency_path) and no_critical and not pending_for_skill
        all_pass = all_pass and passed
        skill_results[skill] = {
            "eligible": enough_cases,
            "stronger_baseline": stronger,
            "quality_gain_points": quality_gain,
            "duration_gain_percent": duration_gain,
            "token_gain_percent": token_gain,
            "quality_path_passed": quality_path,
            "efficiency_path_passed": efficiency_path,
            "no_critical_failures": no_critical,
            "adjudication_pending": pending_for_skill,
            "passed": passed,
            "arms": summaries,
        }
    if missing or adjudication or len(grouped) != 7:
        all_pass = False
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "rater_ids": rater_ids,
        "complete": not missing and not adjudication and len(grouped) == 7,
        "release_gate_passed": all_pass,
        "missing_ratings": missing,
        "adjudication_required": adjudication,
        "adjudication_resolved": adjudication_resolved,
        "skill_results": skill_results,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--rater-id", action="append", required=True)
    parser.add_argument("--adjudicator-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = aggregate(args.run_id, args.rater_id, args.adjudicator_id)
        code = 0 if result["complete"] else 2
    except (OSError, ValueError, RuntimeError) as error:
        result, code = {"status": "error", "error": str(error)}, 1
    body = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    print(body, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
\n