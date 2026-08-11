#!/usr/bin/env python3
"""Validate a publication-figure provenance manifest without opening data files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


HASH = re.compile(r"^[0-9a-fA-F]{64}$")


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for field in ("figure_id", "bounded_conclusion", "route", "backend", "panels", "exports"):
        if payload.get(field) in (None, "", []):
            findings.append({"severity": "error", "field": field, "message": "required field missing"})
    route = payload.get("route")
    if route not in {"data_figure", "assembled_figure", "scientific_schematic", "audit_only"}:
        findings.append({"severity": "error", "field": "route", "message": "invalid route"})
    for index, source in enumerate(payload.get("source_files", []), 1):
        if not source.get("path") or not HASH.match(str(source.get("sha256", ""))):
            findings.append({"severity": "error", "field": f"source_files[{index}]", "message": "path and SHA-256 required"})
    panel_ids: set[str] = set()
    for index, panel in enumerate(payload.get("panels", []), 1):
        panel_id = str(panel.get("panel_id", ""))
        if not panel_id or panel_id in panel_ids:
            findings.append({"severity": "error", "field": f"panels[{index}].panel_id", "message": "missing or duplicate panel ID"})
        panel_ids.add(panel_id)
        for field in ("question", "source_anchor", "panel_type"):
            if panel.get(field) in (None, ""):
                findings.append({"severity": "error", "field": f"panels[{index}].{field}", "message": "required field missing"})
        if panel.get("generated") and route != "scientific_schematic":
            findings.append({"severity": "error", "field": f"panels[{index}].generated", "message": "generated content cannot be a quantitative data panel"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": not errors, "errors": len(errors), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

\n