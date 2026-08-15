#!/usr/bin/env python3
"""Normalize experimental conditions and identify legitimately comparable chemistry records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ORACLE_ID = "chemistry_condition_normalization_v1"


def _measure(value: Any, dimension: str) -> tuple[float, str] | None:
    if not isinstance(value, dict) or not isinstance(value.get("value"), (int, float)) or isinstance(value.get("value"), bool):
        return None
    number, unit = float(value["value"]), str(value.get("unit") or "").casefold().replace("°", "")
    if dimension == "temperature":
        if unit in {"c", "celsius"}:
            return round(number + 273.15, 6), "K"
        if unit in {"k", "kelvin"} and number >= 0:
            return round(number, 6), "K"
    if dimension == "time":
        if unit in {"s", "sec", "second", "seconds"}:
            return round(number, 6), "s"
        if unit in {"min", "minute", "minutes"}:
            return round(number * 60, 6), "s"
        if unit in {"h", "hr", "hour", "hours"}:
            return round(number * 3600, 6), "s"
    if dimension == "pressure":
        if unit in {"pa"}:
            return round(number, 6), "Pa"
        if unit in {"kpa"}:
            return round(number * 1000, 6), "Pa"
        if unit in {"bar"}:
            return round(number * 100000, 6), "Pa"
        if unit in {"atm"}:
            return round(number * 101325, 6), "Pa"
    return None


def _text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def compare(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get("records")
    findings: list[dict[str, str]] = []
    if not isinstance(records, list) or len(records) < 2:
        return {"oracle_id": ORACLE_ID, "valid": False, "normalized_records": [], "pairs": [], "findings": [{"field": "records", "message": "at least two records are required"}]}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(records):
        prefix = f"records[{index}]"
        if not isinstance(row, dict):
            findings.append({"field": prefix, "message": "record must be an object"})
            continue
        record_id = str(row.get("record_id") or "")
        if not record_id or record_id in seen:
            findings.append({"field": f"{prefix}.record_id", "message": "record_id must be unique and non-empty"})
        seen.add(record_id)
        for field in ("entity_id", "endpoint", "basis"):
            if not _text(row.get(field)):
                findings.append({"field": f"{prefix}.{field}", "message": "required comparison identity missing"})
        conditions = row.get("conditions")
        if not isinstance(conditions, dict):
            findings.append({"field": f"{prefix}.conditions", "message": "conditions must be an object"})
            conditions = {}
        normalized_conditions: dict[str, Any] = {}
        for dimension in ("temperature", "time", "pressure"):
            if dimension in conditions:
                converted = _measure(conditions[dimension], dimension)
                if converted is None:
                    findings.append({"field": f"{prefix}.conditions.{dimension}", "message": f"unsupported or invalid {dimension} unit"})
                else:
                    normalized_conditions[dimension] = {"value": converted[0], "unit": converted[1]}
        for field in ("solvent", "catalyst", "atmosphere"):
            normalized_conditions[field] = _text(conditions.get(field))
        normalized.append({
            "record_id": record_id,
            "entity_id": _text(row.get("entity_id")),
            "endpoint": _text(row.get("endpoint")),
            "basis": _text(row.get("basis")),
            "conditions": normalized_conditions,
        })
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(normalized):
        for right in normalized[left_index + 1:]:
            reasons: list[str] = []
            for field in ("entity_id", "endpoint", "basis"):
                if left[field] != right[field]:
                    reasons.append(f"different_{field}")
            for field in ("temperature", "time", "pressure", "solvent", "catalyst", "atmosphere"):
                if left["conditions"].get(field) != right["conditions"].get(field):
                    reasons.append(f"different_{field}")
            pairs.append({"left": left["record_id"], "right": right["record_id"], "comparable": not reasons, "reasons": reasons})
    return {"oracle_id": ORACLE_ID, "valid": len(normalized) >= 2 and not findings, "normalized_records": normalized, "pairs": pairs, "comparable_pairs": sum(row["comparable"] for row in pairs), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = compare(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
