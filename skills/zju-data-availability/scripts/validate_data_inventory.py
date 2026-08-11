#!/usr/bin/env python3
"""Validate dataset-level availability and identifier readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROUTES = {"public_repository", "discipline_repository", "controlled_access", "within_article", "reused_public", "third_party_restricted", "justified_request", "not_applicable"}


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    artifacts = payload.get("artifacts", [])
    ids: set[str] = set()
    for index, item in enumerate(artifacts, 1):
        artifact_id = str(item.get("artifact_id", ""))
        if not artifact_id or artifact_id in ids:
            findings.append({"severity": "error", "field": f"artifacts[{index}].artifact_id", "message": "missing or duplicate artifact ID"})
        ids.add(artifact_id)
        for field in ("description", "supports_claims", "controller", "access_route", "status"):
            if item.get(field) in (None, "", []):
                findings.append({"severity": "error", "field": f"artifacts[{index}].{field}", "message": "required field missing"})
        route = item.get("access_route")
        if route not in ROUTES:
            findings.append({"severity": "error", "field": f"artifacts[{index}].access_route", "message": "invalid access route"})
        if item.get("status") == "ready" and route in {"public_repository", "discipline_repository", "reused_public"}:
            if not item.get("repository") or not item.get("identifier"):
                findings.append({"severity": "error", "field": f"artifacts[{index}]", "message": "ready public artifact lacks repository or identifier"})
        if route in {"controlled_access", "third_party_restricted", "justified_request"} and not item.get("restriction_basis"):
            findings.append({"severity": "error", "field": f"artifacts[{index}].restriction_basis", "message": "restricted route lacks basis"})
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
