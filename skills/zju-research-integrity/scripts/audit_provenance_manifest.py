#!/usr/bin/env python3
"""Audit traceability and preservation fields in a research provenance manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


HASH = re.compile(r"^[0-9a-fA-F]{64}$")


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    artifacts = payload.get("artifacts", [])
    ids: set[str] = set()
    for index, artifact in enumerate(artifacts, 1):
        artifact_id = str(artifact.get("artifact_id", ""))
        if not artifact_id or artifact_id in ids:
            findings.append({"severity": "error", "field": f"artifacts[{index}].artifact_id", "message": "missing or duplicate artifact ID"})
        ids.add(artifact_id)
        for field in ("path", "role", "created_at", "custodian", "preservation_status"):
            if artifact.get(field) in (None, ""):
                findings.append({"severity": "error", "field": f"artifacts[{index}].{field}", "message": "required field missing"})
        if not HASH.match(str(artifact.get("sha256", ""))):
            findings.append({"severity": "error", "field": f"artifacts[{index}].sha256", "message": "valid SHA-256 required"})
        if artifact.get("role") == "raw" and artifact.get("preservation_status") != "immutable_original":
            findings.append({"severity": "error", "field": f"artifacts[{index}].preservation_status", "message": "raw artifact must be preserved as immutable original"})
    for index, link in enumerate(payload.get("links", []), 1):
        if link.get("from_id") not in ids or link.get("to_id") not in ids or not link.get("relation"):
            findings.append({"severity": "error", "field": f"links[{index}]", "message": "invalid provenance link"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {"valid": bool(artifacts) and not errors, "artifacts": len(artifacts), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
