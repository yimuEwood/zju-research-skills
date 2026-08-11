#!/usr/bin/env python3
"""Build condition-aware chemistry evidence groups without averaging unlike records."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


EVIDENCE_STRATA = {
    "experimental": "measured",
    "curated": "curated_measured_or_reported",
    "submitted": "submitted",
    "computed": "computed",
    "predicted": "predicted",
    "vendor": "vendor",
    "regulatory": "regulatory",
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _canonical_conditions(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip().lower().replace(" ", "_"): (
            _text(item).casefold() if isinstance(item, str) else item
        )
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        if item not in (None, "")
    }


def _group_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": _text(record.get("entity_id")),
        "property_or_endpoint": _text(record.get("property_or_endpoint")).casefold(),
        "unit": _text(record.get("unit")).casefold(),
        "basis": _text(record.get("basis")).casefold(),
        "conditions": _canonical_conditions(record.get("conditions")),
        "evidence_stratum": EVIDENCE_STRATA.get(
            _text(record.get("evidence_type")).casefold(), "unknown"
        ),
        "method_or_assay": _text(
            record.get("method_or_assay", record.get("method", record.get("assay_type")))
        ).casefold(),
    }


def _group_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "CG-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def build(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"valid": False, "findings": ["input must be an object"], "matrix_rows": []}
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        return {"valid": False, "findings": ["records must be a non-empty array"], "matrix_rows": []}

    findings: list[str] = []
    rows: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_record_ids: set[str] = set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            findings.append(f"records[{index}] is not an object")
            continue
        record_id = _text(record.get("record_id"))
        if not record_id:
            findings.append(f"records[{index}] lacks record_id")
            continue
        if record_id in seen_record_ids:
            findings.append(f"duplicate record_id:{record_id}")
            continue
        seen_record_ids.add(record_id)
        group_payload = _group_payload(record)
        group_id = _group_id(group_payload)
        relation = _text(record.get("relation_operator")) or "="
        row = {
            "record_id": record_id,
            "entity_id": group_payload["entity_id"],
            "property_or_endpoint": record.get("property_or_endpoint"),
            "relation_operator": relation,
            "value": record.get("value"),
            "unit": record.get("unit"),
            "basis": record.get("basis"),
            "conditions": record.get("conditions", {}),
            "method_or_assay": record.get(
                "method_or_assay", record.get("method", record.get("assay_type"))
            ),
            "evidence_type": record.get("evidence_type"),
            "evidence_stratum": group_payload["evidence_stratum"],
            "source_database": record.get("source_database"),
            "source_anchor": record.get("source_anchor"),
            "uncertainty": record.get("uncertainty"),
            "comparison_group_id": group_id,
        }
        rows.append(row)
        grouped[group_id].append(row)

    groups = []
    for group_id, members in sorted(grouped.items()):
        exact_values = {
            json.dumps(
                [member.get("relation_operator"), member.get("value")],
                ensure_ascii=False,
                sort_keys=True,
            )
            for member in members
        }
        groups.append(
            {
                "comparison_group_id": group_id,
                "record_ids": [member["record_id"] for member in members],
                "record_count": len(members),
                "directly_comparable": True,
                "heterogeneous_reported_values": len(exact_values) > 1,
                "synthesis_rule": (
                    "review method, uncertainty, sample and primary sources; do not average automatically"
                    if len(exact_values) > 1
                    else "retain the reported value and provenance; no automatic averaging"
                ),
            }
        )

    return {
        "valid": bool(rows) and not findings,
        "findings": findings,
        "matrix_rows": rows,
        "comparison_groups": groups,
        "non_comparability_rule": (
            "Records with different entity, property, unit, basis, conditions, evidence stratum, "
            "or method/assay are assigned to different groups and must not be pooled automatically."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build(json.loads(args.input.read_text(encoding="utf-8")))
    body = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
