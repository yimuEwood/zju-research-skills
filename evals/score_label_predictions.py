#!/usr/bin/env python3
"""Score predicted issue labels for a fixture containing expected_checks arrays."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path, help="JSON list of {id, detected_checks}")
    parser.add_argument("--minimum-recall", type=float, default=0.9)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
    cases = fixture.get("cases", fixture.get("fixtures", []))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8-sig"))
    pred_by_id = {item["id"]: set(item.get("detected_checks", [])) for item in predictions}
    true_positive = false_negative = false_positive = 0
    per_case = []
    for case in cases:
        expected = set(case.get("expected_checks", []))
        detected = pred_by_id.get(case["id"], set())
        tp = len(expected & detected)
        fn = len(expected - detected)
        fp = len(detected - expected)
        true_positive += tp
        false_negative += fn
        false_positive += fp
        per_case.append({"id": case["id"], "missing": sorted(expected - detected), "unexpected": sorted(detected - expected)})
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    result = {
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "passes_recall_gate": recall >= args.minimum_recall,
        "missing_prediction_cases": sorted(case["id"] for case in cases if case["id"] not in pred_by_id),
        "per_case": per_case,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passes_recall_gate"] and not result["missing_prediction_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

\n