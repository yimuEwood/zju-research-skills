#!/usr/bin/env python3
"""Render structured experiment intake JSON as traceable Markdown/YAML."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"password", "passwd", "cookie", "token", "secret", "credential", "session"}
SECTIONS = [
    ("Objective", "objective"),
    ("Materials and samples", "materials_and_samples"),
    ("Procedure", "procedure"),
    ("Deviations", "deviations"),
    ("Observations", "observations"),
    ("Results", "results"),
    ("Interpretation", "interpretation"),
    ("Anomalies", "anomalies"),
    ("Next actions", "next_actions"),
]


def quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def find_sensitive(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).casefold() in SENSITIVE_KEYS and item not in (None, "", False):
                found.append(child)
            found.extend(find_sensitive(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_sensitive(item, f"{path}[{index}]"))
    return found


def file_entry(item: Any) -> dict[str, str]:
    raw = item if isinstance(item, dict) else {"path": str(item)}
    path = Path(str(raw.get("path", "")))
    digest = "unavailable"
    if path.is_file():
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "media_type": str(raw.get("media_type", "unknown")),
        "note": str(raw.get("note", "")),
    }


def bullets(value: Any) -> str:
    if value in (None, "", []):
        return "- unknown"
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def render(data: dict[str, Any]) -> str:
    sensitive = find_sensitive(data)
    if sensitive:
        raise ValueError("Sensitive credential-like fields are not allowed: " + ", ".join(sensitive))
    files = [file_entry(item) for item in data.get("source_files", [])]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "---",
        'schema_version: "1.0"',
        f"experiment_id: {quote(data.get('experiment_id', 'unknown'))}",
        f"title: {quote(data.get('title', 'Untitled experiment'))}",
        f"project: {quote(data.get('project', 'unknown'))}",
        f"operator: {quote(data.get('operator', 'unknown'))}",
        f"experiment_started_at: {quote(data.get('experiment_started_at', 'unknown'))}",
        f"record_created_at: {quote(data.get('record_created_at', now))}",
        f"status: {quote(data.get('status', 'partial'))}",
        "sample_ids:" if data.get("sample_ids") else "sample_ids: []",
    ]
    lines.extend(f"  - {quote(item)}" for item in data.get("sample_ids", []))
    lines.append("tags:" if data.get("tags") else "tags: []")
    lines.extend(f"  - {quote(item)}" for item in data.get("tags", []))
    lines.append("source_files:" if files else "source_files: []")
    for item in files:
        lines.extend([
            f"  - path: {quote(item['path'])}",
            f"    sha256: {quote(item['sha256'])}",
            f"    media_type: {quote(item['media_type'])}",
            f"    note: {quote(item['note'])}",
        ])
    lines.extend(["---", "", f"# {data.get('title', 'Untitled experiment')}", ""])
    for heading, key in SECTIONS:
        lines.extend([f"## {heading}", "", bullets(data.get(key)), ""])
    lines.extend(["## Source manifest", ""])
    if files:
        lines.extend(f"- `{item['path']}` — SHA-256: `{item['sha256']}`; type: {item['media_type']}; {item['note']}" for item in files)
    else:
        lines.append("- No source files supplied.")
    lines.extend(["", "## Amendments", "", "- None.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    output = render(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "source_files": len(data.get('source_files', []))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
