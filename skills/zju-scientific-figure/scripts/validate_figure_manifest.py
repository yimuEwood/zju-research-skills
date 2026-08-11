#!/usr/bin/env python3
"""Validate local files and traceability in a publication-figure manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


HASH = re.compile(r"^[0-9a-fA-F]{64}$")
PLACEHOLDER_ANCHOR = re.compile(
    r"^(?:anchor|source)[_-]?(?:required|pending)$|"
    r"^(?:todo|tbd|placeholder|example|fake|unknown|none|null|n/?a)$",
    re.IGNORECASE,
)
FORMAT_ALIASES = {
    "jpeg": "jpg",
    "tif": "tiff",
}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of *path* without loading the whole file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_local_path(value: object, base_dir: Path) -> Path | None:
    """Resolve a manifest path; relative paths are anchored at *base_dir*."""

    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def normalized_format(value: object) -> str:
    declared = str(value or "").strip().lower().lstrip(".")
    return FORMAT_ALIASES.get(declared, declared)


def signature_matches(path: Path, declared_format: str) -> bool | None:
    """Check common export signatures; return None for unsupported formats."""

    with path.open("rb") as handle:
        head = handle.read(4096)
    checks = {
        "pdf": lambda data: data.startswith(b"%PDF-"),
        "png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
        "tiff": lambda data: data.startswith((b"II*\x00", b"MM\x00*")),
        "eps": lambda data: data.startswith(b"%!PS-Adobe"),
        "svg": lambda data: re.search(
            br"<svg(?:\s|>)",
            data.lstrip(b"\xef\xbb\xbf\t\r\n "),
            re.IGNORECASE,
        ) is not None,
    }
    check = checks.get(declared_format)
    return None if check is None else check(head)


def anchor_references_source(anchor: str, source_paths: list[str]) -> bool:
    normalized_anchor = anchor.casefold().replace("\\", "/")
    for source_path in source_paths:
        normalized_source = source_path.casefold().replace("\\", "/")
        source_name = normalized_source.rsplit("/", 1)[-1]
        if normalized_source in normalized_anchor or source_name in normalized_anchor:
            return True
    return False


def validate(payload: dict[str, Any], base_dir: Path | str = ".") -> dict[str, Any]:
    """Validate deterministic artifact claims relative to an explicit base directory.

    This function verifies structure, local file existence, digests, source anchors,
    and common export signatures. It deliberately does not claim that visual or
    scientific semantics were inspected.
    """

    root = Path(base_dir).expanduser().resolve()
    findings: list[dict[str, str]] = []
    for field in ("figure_id", "bounded_conclusion", "route", "backend", "panels", "exports"):
        if payload.get(field) in (None, "", []):
            findings.append({"severity": "error", "field": field, "message": "required field missing"})

    route = payload.get("route")
    if route not in {"data_figure", "assembled_figure", "scientific_schematic", "audit_only"}:
        findings.append({"severity": "error", "field": "route", "message": "invalid route"})

    sources = payload.get("source_files", [])
    if not isinstance(sources, list):
        findings.append({"severity": "error", "field": "source_files", "message": "must be a list"})
        sources = []
    if route == "data_figure" and not sources:
        findings.append({
            "severity": "error",
            "field": "source_files",
            "message": "data figures require at least one declared source file",
        })

    declared_source_paths: list[str] = []
    verified_sources = 0
    for index, source in enumerate(sources, 1):
        field = f"source_files[{index}]"
        if not isinstance(source, dict):
            findings.append({"severity": "error", "field": field, "message": "source entry must be an object"})
            continue
        declared_path = source.get("path")
        declared_hash = str(source.get("sha256", ""))
        if isinstance(declared_path, str) and declared_path.strip():
            declared_source_paths.append(declared_path.strip())
        resolved = resolve_local_path(declared_path, root)
        if resolved is None:
            findings.append({"severity": "error", "field": f"{field}.path", "message": "path required"})
        elif not resolved.is_file():
            findings.append({
                "severity": "error",
                "field": f"{field}.path",
                "message": f"local source file does not exist: {resolved}",
            })
        if not HASH.fullmatch(declared_hash):
            findings.append({"severity": "error", "field": f"{field}.sha256", "message": "valid SHA-256 required"})
        elif resolved is not None and resolved.is_file():
            try:
                actual_hash = sha256_file(resolved)
            except OSError as exc:
                findings.append({
                    "severity": "error",
                    "field": f"{field}.sha256",
                    "message": f"could not read source file: {exc}",
                })
            else:
                if actual_hash.casefold() != declared_hash.casefold():
                    findings.append({
                        "severity": "error",
                        "field": f"{field}.sha256",
                        "message": f"SHA-256 mismatch: computed {actual_hash}",
                    })
                else:
                    verified_sources += 1

    panels = payload.get("panels", [])
    if not isinstance(panels, list):
        findings.append({"severity": "error", "field": "panels", "message": "must be a list"})
        panels = []
    panel_ids: set[str] = set()
    for index, panel in enumerate(panels, 1):
        field = f"panels[{index}]"
        if not isinstance(panel, dict):
            findings.append({"severity": "error", "field": field, "message": "panel entry must be an object"})
            continue
        panel_id = str(panel.get("panel_id", ""))
        if not panel_id or panel_id in panel_ids:
            findings.append({"severity": "error", "field": f"{field}.panel_id", "message": "missing or duplicate panel ID"})
        panel_ids.add(panel_id)
        for required in ("question", "source_anchor", "panel_type"):
            if panel.get(required) in (None, ""):
                findings.append({"severity": "error", "field": f"{field}.{required}", "message": "required field missing"})
        anchor = str(panel.get("source_anchor", "")).strip()
        if anchor and PLACEHOLDER_ANCHOR.fullmatch(anchor):
            findings.append({"severity": "error", "field": f"{field}.source_anchor", "message": "placeholder source anchor rejected"})
        elif route == "data_figure" and anchor and not anchor_references_source(anchor, declared_source_paths):
            findings.append({
                "severity": "error",
                "field": f"{field}.source_anchor",
                "message": "data-panel anchor does not reference a declared source file",
            })
        if panel.get("generated") and route != "scientific_schematic":
            findings.append({"severity": "error", "field": f"{field}.generated", "message": "generated content cannot be a quantitative data panel"})

    exports = payload.get("exports", [])
    if not isinstance(exports, list):
        findings.append({"severity": "error", "field": "exports", "message": "must be a list"})
        exports = []
    verified_exports = 0
    for index, export in enumerate(exports, 1):
        field = f"exports[{index}]"
        export_verified = True
        if isinstance(export, str):
            declared_path = export
            explicit_format = ""
            declared_hash = ""
        elif isinstance(export, dict):
            declared_path = export.get("path")
            explicit_format = normalized_format(export.get("format"))
            declared_hash = str(export.get("sha256", ""))
        else:
            findings.append({"severity": "error", "field": field, "message": "export must be a path or object"})
            continue

        resolved = resolve_local_path(declared_path, root)
        extension_format = normalized_format(Path(str(declared_path or "")).suffix)
        declared_format = explicit_format or extension_format
        if explicit_format and extension_format and explicit_format != extension_format:
            findings.append({"severity": "error", "field": f"{field}.format", "message": "declared format disagrees with file extension"})
            export_verified = False
        if not declared_format:
            findings.append({"severity": "error", "field": f"{field}.format", "message": "export format must be declared or present in the extension"})
            export_verified = False
        if resolved is None:
            findings.append({"severity": "error", "field": f"{field}.path", "message": "export path required"})
            continue
        if not resolved.is_file():
            findings.append({"severity": "error", "field": f"{field}.path", "message": f"local export does not exist: {resolved}"})
            continue

        signature_read_error = False
        try:
            signature = signature_matches(resolved, declared_format)
        except OSError as exc:
            findings.append({"severity": "error", "field": f"{field}.path", "message": f"could not read export: {exc}"})
            export_verified = False
            signature = None
            signature_read_error = True
        if signature is False:
            findings.append({"severity": "error", "field": f"{field}.format", "message": f"file signature does not match {declared_format}"})
            export_verified = False
        elif signature is None and not signature_read_error:
            findings.append({
                "severity": "error",
                "field": f"{field}.format",
                "message": f"no deterministic signature check implemented for {declared_format}",
            })
            export_verified = False
        if declared_hash:
            if not HASH.fullmatch(declared_hash):
                findings.append({"severity": "error", "field": f"{field}.sha256", "message": "valid SHA-256 required when declared"})
                export_verified = False
            else:
                try:
                    actual_hash = sha256_file(resolved)
                except OSError as exc:
                    findings.append({"severity": "error", "field": f"{field}.sha256", "message": f"could not read export: {exc}"})
                    export_verified = False
                else:
                    if actual_hash.casefold() != declared_hash.casefold():
                        findings.append({"severity": "error", "field": f"{field}.sha256", "message": f"SHA-256 mismatch: computed {actual_hash}"})
                        export_verified = False
        if export_verified:
            verified_exports += 1

    deterministic_exports_complete = bool(exports) and verified_exports == len(exports)
    if exports and not deterministic_exports_complete:
        findings.append({
            "severity": "error",
            "field": "exports",
            "message": "every declared export must pass deterministic existence and format verification",
        })

    findings.append({
        "severity": "not_assessed",
        "field": "semantic_inspection",
        "message": "visual correctness, data-to-mark fidelity, and scientific interpretation were not inspected",
    })
    errors = [item for item in findings if item["severity"] == "error"]
    return {
        "valid": not errors,
        "errors": len(errors),
        "base_dir": str(root),
        "verified_sources": verified_sources,
        "verified_exports": verified_exports,
        "deterministic_exports_complete": deterministic_exports_complete,
        "semantic_inspection": "not_performed",
        "publication_ready": False,
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
