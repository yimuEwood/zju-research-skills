#!/usr/bin/env python3
"""Score field-level reference-audit predictions against the synthetic fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def score(fixture: dict, predictions: list[dict]) -> dict[str, object]:
    gold_by_id = {item["audit_id"]: item for item in fixture["records"]}
    pred_by_id = {item["audit_id"]: item for item in predictions}
    tp = fp = fn = tn = 0
    missing_records: list[str] = []
    for audit_id, gold in gold_by_id.items():
        prediction = pred_by_id.get(audit_id)
        if not prediction:
            missing_records.append(audit_id)
            fn += len(gold["expected_error_fields"])
            continue
        statuses = {row["field"]: row["status"] for row in prediction.get("comparisons", [])}
        checked = set(gold.get("submitted", {})) & set(gold.get("canonical", {}))
        expected = set(gold["expected_error_fields"])
        for field in checked:
            predicted_error = statuses.get(field) == "material_mismatch"
            actual_error = field in expected
            if predicted_error and actual_error:
                tp += 1
            elif predicted_error and not actual_error:
                fp += 1
            elif not predicted_error and actual_error:
                fn += 1
            else:
                tn += 1
    recall = tp / (tp + fn) if tp + fn else 1.0
    false_positive_rate = fp / (fp + tn) if fp + tn else 0.0
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "error_detection_rate": round(recall, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "passes_detection_gate": recall >= 0.95,
        "passes_false_positive_gate": false_positive_rate <= 0.05,
        "missing_records": missing_records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures/reference-audit-30.json")
    parser.add_argument("--predictions", required=True, type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8-sig"))
    result = score(fixture, predictions)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passes_detection_gate"] and result["passes_false_positive_gate"] and not result["missing_records"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

\n