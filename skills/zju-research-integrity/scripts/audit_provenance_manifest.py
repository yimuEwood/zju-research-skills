#!/usr/bin/env python3
"""Audit local artifact existence, digests, and links in a provenance manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


HASH = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of *path* without loading the whole file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_local_path(value: object, base_dir: Path) -> Path | None:
    """Resolve relative manifest paths underneath the explicitly selected base."""

    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def validate(payload: dict[str, Any], base_dir: Path | str = ".") -> dict[str, Any]:
    """Validate provenance metadata and recompute every declared local digest.

    Relative artifact paths are resolved against ``base_dir``. Absolute paths are
    used as declared. The default is the current directory for library callers;
    the CLI instead defaults to the input manifest's directory.
    """

    root = Path(base_dir).expanduser().resolve()
    findings: list[dict[str, str]] = []
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        findings.append({"severity": "error", "field": "artifacts", "message": "must be a list"})
        artifacts = []
    ids: set[str] = set()
    verified_artifacts = 0
    for index, artifact in enumerate(artifacts, 1):
        field = f"artifacts[{index}]"
        if not isinstance(artifact, dict):
            findings.append({"severity": "error", "field": field, "message": "artifact entry must be an object"})
            continue
        artifact_error_count = len(findings)
        artifact_id = str(artifact.get("artifact_id", ""))
        if not artifact_id or artifact_id in ids:
            findings.append({"severity": "error", "field": f"{field}.artifact_id", "message": "missing or duplicate artifact ID"})
        ids.add(artifact_id)
        for required in ("path", "role", "created_at", "custodian", "preservation_status"):
            if artifact.get(required) in (None, ""):
                findings.append({"severity": "error", "field": f"{field}.{required}", "message": "required field missing"})

        resolved = resolve_local_path(artifact.get("path"), root)
        declared_hash = str(artifact.get("sha256", ""))
        if resolved is None:
            findings.append({"severity": "error", "field": f"{field}.path", "message": "path required"})
        elif not resolved.is_file():
            findings.append({"severity": "error", "field": f"{field}.path", "message": f"local artifact file does not exist: {resolved}"})
        if not HASH.fullmatch(declared_hash):
            findings.append({"severity": "error", "field": f"{field}.sha256", "message": "valid SHA-256 required"})
        elif resolved is not None and resolved.is_file():
            try:
                actual_hash = sha256_file(resolved)
            except OSError as exc:
                findings.append({
                    "severity": "error",
                    "field": f"{field}.sha256",
                    "message": f"could not read artifact: {exc}",
                })
            else:
                if actual_hash.casefold() != declared_hash.casefold():
                    findings.append({
                        "severity": "error",
                        "field": f"{field}.sha256",
                        "message": f"SHA-256 mismatch: computed {actual_hash}",
                    })
        if artifact.get("role") == "raw" and artifact.get("preservation_status") != "immutable_original":
            findings.append({"severity": "error", "field": f"{field}.preservation_status", "message": "raw artifact must be preserved as immutable original"})
        if len(findings) == artifact_error_count:
            verified_artifacts += 1

    links = payload.get("links", [])
    if not isinstance(links, list):
        findings.append({"severity": "error", "field": "links", "message": "must be a list"})
        links = []
    for index, link in enumerate(links, 1):
        if not isinstance(link, dict) or link.get("from_id") not in ids or link.get("to_id") not in ids or not link.get("relation"):
            findings.append({"severity": "error", "field": f"links[{index}]", "message": "invalid provenance link"})
    errors = [item for item in findings if item["severity"] == "error"]
    return {
        "valid": bool(artifacts) and not errors,
        "artifacts": len(artifacts),
        "verified_artifacts": verified_artifacts,
        "base_dir": str(root),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--base-dir",
        type=Path,
        help="resolve relative artifact paths here (default: the manifest file's directory)",
    )
    args = parser.parse_args()
    input_path = args.input.resolve()
    base_dir = args.base_dir if args.base_dir is not None else input_path.parent
    result = validate(json.loads(input_path.read_text(encoding="utf-8")), base_dir=base_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
