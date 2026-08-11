#!/usr/bin/env python3
"""Validate a source-grounded paper-to-presentation plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for field in ("project_id", "source_id", "paper_type", "audience", "duration_minutes", "slides", "terminology_ledger"):
        if payload.get(field) in (None, "", []):
            findings.append({"severity": "error", "field": field, "message": "required field missing"})
    slides = payload.get("slides", [])
    ids: set[str] = set()
    seconds = 0.0
    for index, slide in enumerate(slides, 1):
        slide_id = str(slide.get("slide_id", ""))
        if not slide_id or slide_id in ids:
            findings.append({"severity": "error", "field": f"slides[{index}].slide_id", "message": "missing or duplicate slide ID"})
        ids.add(slide_id)
        for field in ("title", "claim", "source_anchors", "speaker_notes", "estimated_seconds"):
            if slide.get(field) in (None, "", []):
                findings.append({"severity": "error", "field": f"slides[{index}].{field}", "message": "required field missing"})
        try:
            seconds += float(slide.get("estimated_seconds", 0))
        except (TypeError, ValueError):
            findings.append({"severity": "error", "field": f"slides[{index}].estimated_seconds", "message": "must be numeric"})
    try:
        budget = float(payload.get("duration_minutes", 0)) * 60
        if seconds > budget and budget > 0:
            findings.append({"severity": "error", "field": "slides", "message": "estimated speaking time exceeds duration"})
    except (TypeError, ValueError):
        findings.append({"severity": "error", "field": "duration_minutes", "message": "must be numeric"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(slides) and not errors, "estimated_seconds": seconds, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
