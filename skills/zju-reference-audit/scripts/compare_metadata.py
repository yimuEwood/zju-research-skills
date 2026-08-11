#!/usr/bin/env python3
"""Compare submitted and canonical reference metadata from local JSON."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalized(value: Any) -> str:
    value = unicodedata.normalize("NFKC", clean(value)).casefold()
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def doi(value: Any) -> str:
    return DOI_PREFIX.sub("", clean(value)).casefold().rstrip(".,;)")


def authors(value: Any) -> list[str]:
    if isinstance(value, list):
        source = value
    else:
        source = re.split(r"\s*;\s*", clean(value))
    return [normalized(item) for item in source if normalized(item)]


def compare_field(field: str, submitted: Any, canonical: Any) -> dict[str, Any]:
    if canonical in (None, "", []):
        return {"field": field, "submitted": submitted, "canonical": canonical, "status": "not_verified", "similarity": None}
    if field == "doi":
        left, right = doi(submitted), doi(canonical)
        similarity = 1.0 if left == right else 0.0
    elif field == "authors":
        left, right = authors(submitted), authors(canonical)
        similarity = difflib.SequenceMatcher(None, left, right).ratio()
    else:
        left, right = normalized(submitted), normalized(canonical)
        similarity = difflib.SequenceMatcher(None, left, right).ratio()
    if left == right:
        status = "match"
    elif field in {"title", "venue"} and similarity >= 0.94:
        status = "minor_difference"
    else:
        status = "material_mismatch"
    return {"field": field, "submitted": submitted, "canonical": canonical, "status": status, "similarity": round(similarity, 3)}


def audit(item: dict[str, Any], index: int) -> dict[str, Any]:
    submitted = item.get("submitted", {})
    canonical = item.get("canonical", {})
    fields = ["doi", "title", "authors", "year", "venue", "volume", "issue", "pages"]
    comparisons = [compare_field(field, submitted.get(field), canonical.get(field)) for field in fields]
    return {
        "audit_id": item.get("audit_id") or f"REF-{index:04d}",
        "comparisons": comparisons,
        "material_mismatches": sum(row["status"] == "material_mismatch" for row in comparisons),
        "source_urls": item.get("source_urls", []),
        "human_confirmation_required": any(row["status"] in {"material_mismatch", "not_verified"} for row in comparisons),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        items = data
    elif isinstance(data.get("records"), list):
        items = data["records"]
    else:
        items = [data]
    result = [audit(item, index) for index, item in enumerate(items, 1)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"audited": len(result), "material_mismatches": sum(x["material_mismatches"] for x in result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
\n