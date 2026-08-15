#!/usr/bin/env python3
"""Transparent, deterministic multi-lane candidate prioritization."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


VERSION = "1.0"
LANE_WEIGHTS = {
    "genetic": 1.2,
    "disease": 1.1,
    "tractability": 1.0,
    "mechanism": 1.0,
    "assay_reproducibility": 1.2,
    "translational": 1.1,
    "developability": 1.0,
}


class CandidateInputError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _potency_score(value: object) -> float | None:
    if value is None:
        return None
    potency = float(value)
    if not math.isfinite(potency) or potency <= 0:
        raise CandidateInputError("potency_nM must be a positive finite number")
    return _clamp((6.0 - math.log10(potency)) / 5.0)


def _selectivity_score(value: object) -> float | None:
    if value is None:
        return None
    ratio = float(value)
    if not math.isfinite(ratio) or ratio <= 0:
        raise CandidateInputError("selectivity_ratio must be a positive finite number")
    return _clamp(math.log10(ratio) / 3.0)


def _prepare(candidate: dict) -> dict:
    for field in ("candidate_id", "target_id", "compound_id", "evidence"):
        if field not in candidate:
            raise CandidateInputError(f"candidate is missing {field}")
    identifiers = {}
    for field in ("candidate_id", "target_id", "compound_id"):
        value = candidate[field]
        if not isinstance(value, str) or not value.strip():
            raise CandidateInputError(f"{field} must be a non-empty string")
        identifiers[field] = value.strip()
    candidate_id = identifiers["candidate_id"]
    if not isinstance(candidate["evidence"], list) or not candidate["evidence"]:
        raise CandidateInputError(f"evidence must be a non-empty array for {candidate_id}")
    lanes: dict[str, list[float]] = {}
    source_ids: dict[str, list[str]] = {}
    for record in candidate["evidence"]:
        try:
            lane = str(record["lane"])
            value = float(record["value"])
            raw_source_id = record["source_id"]
            if not isinstance(raw_source_id, str) or not raw_source_id.strip():
                raise CandidateInputError(f"evidence for {candidate_id} needs a non-empty source_id")
            source_id = raw_source_id.strip()
        except CandidateInputError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise CandidateInputError(f"invalid evidence record for {candidate_id}") from exc
        if lane not in LANE_WEIGHTS:
            raise CandidateInputError(f"unsupported evidence lane for {candidate_id}: {lane}")
        if not source_id or not math.isfinite(value) or not 0 <= value <= 1:
            raise CandidateInputError(f"evidence for {candidate_id} needs source_id and value in [0,1]")
        lanes.setdefault(lane, []).append(value)
        source_ids.setdefault(lane, []).append(source_id)
    aggregated = {lane: sum(values) / len(values) for lane, values in lanes.items()}
    safety_flags = sorted({str(value) for value in candidate.get("safety_flags", []) if str(value)})
    return {
        "candidate_id": candidate_id,
        "target_id": identifiers["target_id"],
        "compound_id": identifiers["compound_id"],
        "lanes": aggregated,
        "source_ids": {lane: sorted(set(values)) for lane, values in source_ids.items()},
        "potency": _potency_score(candidate.get("potency_nM")),
        "selectivity": _selectivity_score(candidate.get("selectivity_ratio")),
        "safety_flags": safety_flags,
        "hard_exclusion": _strict_bool(candidate.get("hard_exclusion", False), "hard_exclusion"),
    }


def _strict_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise CandidateInputError(f"{field} must be a JSON boolean")


def _score(record: dict, omitted_lane: str | None = None) -> tuple[float, dict]:
    weighted = 0.0
    total_weight = 0.0
    missing = []
    components = {}
    for lane, weight in LANE_WEIGHTS.items():
        if lane == omitted_lane:
            continue
        total_weight += weight
        value = record["lanes"].get(lane)
        if value is None:
            value = 0.0
            missing.append(lane)
        components[lane] = round(value, 10)
        weighted += weight * value
    for name, weight in (("potency", 0.8), ("selectivity", 0.5)):
        total_weight += weight
        value = record[name]
        if value is None:
            value = 0.0
            missing.append(name)
        components[name] = round(value, 10)
        weighted += weight * value
    base = weighted / total_weight if total_weight else 0.0
    coverage = 1.0 - len(missing) / (len(LANE_WEIGHTS) - (1 if omitted_lane else 0) + 2)
    safety_penalty = min(0.45, 0.12 * len(record["safety_flags"]))
    score = 100.0 * _clamp(base * (0.65 + 0.35 * coverage) - safety_penalty)
    if record["hard_exclusion"]:
        score = 0.0
    return score, {
        "normalized": components,
        "coverage": round(coverage, 10),
        "missing": missing,
        "safety_penalty": round(safety_penalty, 10),
    }


def prioritize(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateInputError(f"invalid candidate JSON: {exc}") from exc
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates:
        raise CandidateInputError("input requires a non-empty candidates array")
    records = [_prepare(candidate) for candidate in candidates]
    ids = [record["candidate_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise CandidateInputError("candidate_id values must be unique")

    base_scores = {record["candidate_id"]: _score(record) for record in records}
    order = sorted(records, key=lambda item: (-base_scores[item["candidate_id"]][0], item["candidate_id"]))
    rank_samples = {candidate_id: [] for candidate_id in ids}
    for omitted in [None, *LANE_WEIGHTS]:
        scenario = sorted(records, key=lambda item: (-_score(item, omitted)[0], item["candidate_id"]))
        for rank, record in enumerate(scenario, start=1):
            rank_samples[record["candidate_id"]].append(rank)

    ranked = []
    for rank, record in enumerate(order, start=1):
        score, detail = base_scores[record["candidate_id"]]
        ranked.append({
            "rank": rank,
            "candidate_id": record["candidate_id"],
            "target_id": record["target_id"],
            "compound_id": record["compound_id"],
            "score": round(score, 8),
            "score_components": detail,
            "source_ids": record["source_ids"],
            "safety_flags": record["safety_flags"],
            "hard_exclusion": record["hard_exclusion"],
            "leave_one_lane_out_rank_range": [min(rank_samples[record["candidate_id"]]), max(rank_samples[record["candidate_id"]])],
        })
    return {
        "schema_version": "1.0",
        "decision_class": "decision_support_not_validation",
        "executor": {"name": "prioritize_candidates", "version": VERSION},
        "source": {"path": str(path), "sha256": _sha256(path)},
        "weights": LANE_WEIGHTS,
        "ranking": ranked,
        "sensitivity_scenarios": ["all_lanes", *[f"omit_{lane}" for lane in LANE_WEIGHTS]],
        "limitations": [
            "normalized evidence values are user-supplied and must remain traceable to raw records",
            "the heuristic is not a validated efficacy, safety, clinical, regulatory, or investment model",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = prioritize(Path(args.input))
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, CandidateInputError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
