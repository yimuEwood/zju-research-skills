#!/usr/bin/env python3
"""Keep invention evidence, pre-cutoff prior art, and later background in separate lanes."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any


ORACLE_ID = "patent_prior_art_lane_separation_v1"


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def separate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    cutoff = _date(payload.get("critical_date"))
    if cutoff is None:
        findings.append({"field": "critical_date", "message": "critical_date must be YYYY-MM-DD"})
    invention_sources = payload.get("invention_source_ids")
    if not isinstance(invention_sources, list) or not invention_sources:
        findings.append({"field": "invention_source_ids", "message": "at least one invention source is required"})
        invention_sources = []
    records = payload.get("candidate_records")
    if not isinstance(records, list) or not records:
        findings.append({"field": "candidate_records", "message": "at least one candidate record is required"})
        records = []
    lanes = {"prior_art_candidate": [], "post_cutoff_background": [], "date_unresolved": []}
    seen: set[str] = set()
    for index, row in enumerate(records):
        prefix = f"candidate_records[{index}]"
        if not isinstance(row, dict):
            findings.append({"field": prefix, "message": "record must be an object"})
            continue
        record_id = str(row.get("record_id") or "")
        source_id = str(row.get("source_id") or "")
        if not record_id or record_id in seen:
            findings.append({"field": f"{prefix}.record_id", "message": "record_id must be unique and non-empty"})
        seen.add(record_id)
        if source_id in {str(item) for item in invention_sources}:
            findings.append({"field": f"{prefix}.source_id", "message": "an invention source cannot also be classified as prior art"})
        publication = _date(row.get("publication_date"))
        if publication is None:
            computed = "date_unresolved"
        elif cutoff is not None and publication <= cutoff:
            computed = "prior_art_candidate"
        else:
            computed = "post_cutoff_background"
        if row.get("declared_lane") != computed:
            findings.append({"field": f"{prefix}.declared_lane", "message": f"declared {row.get('declared_lane')!r}, computed {computed!r}"})
        if computed == "prior_art_candidate" and not isinstance(row.get("matched_feature_ids"), list):
            findings.append({"field": f"{prefix}.matched_feature_ids", "message": "prior-art candidate requires an explicit matched-feature list"})
        if any(row.get(field) not in (None, "", "not_assessed") for field in ("novelty_conclusion", "patentability_conclusion", "freedom_to_operate_conclusion")):
            findings.append({"field": prefix, "message": "technical prior-art lanes cannot contain legal conclusions"})
        lanes[computed].append(record_id)
    return {"oracle_id": ORACLE_ID, "valid": bool(records) and cutoff is not None and not findings, "critical_date": str(cutoff) if cutoff else None, "lanes": lanes, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = separate(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
