#!/usr/bin/env python3
"""Validate dated correction/retraction status records without network access."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


def parse_date(value: str) -> date:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return date.fromisoformat(normalized)


def validate(records: list[dict[str, Any]], as_of: date, max_age_days: int) -> dict[str, Any]:
    rows = []
    for index, record in enumerate(records, 1):
        audit_id = str(record.get("audit_id") or f"row-{index}")
        issues: list[str] = []
        checked_raw = record.get("status_checked_at")
        checked: date | None = None
        if not checked_raw:
            issues.append("status_checked_at is missing")
        else:
            try:
                checked = parse_date(str(checked_raw))
            except ValueError:
                issues.append("status_checked_at is not valid ISO-8601")
        sources = record.get("status_sources")
        if not isinstance(sources, list) or not any(str(item).strip() for item in sources):
            issues.append("status_sources must contain at least one source")
        if not record.get("version_status"):
            issues.append("version_status is missing")
        age_days = (as_of - checked).days if checked else None
        if age_days is not None and age_days < 0:
            issues.append("status_checked_at is in the future")
        freshness = "not_verified" if issues else ("stale" if age_days is not None and age_days > max_age_days else "current")
        rows.append({"audit_id": audit_id, "freshness": freshness, "age_days": age_days, "issues": issues})
    valid = all(row["freshness"] == "current" for row in rows)
    return {"valid": valid, "as_of": as_of.isoformat(), "max_age_days": max_age_days, "records": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--max-age-days", type=int, default=90)
    args = parser.parse_args()
    if args.max_age_days < 0:
        parser.error("--max-age-days must be non-negative")
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    records = data if isinstance(data, list) else data.get("records", [])
    result = validate(records, parse_date(args.as_of), args.max_age_days)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
