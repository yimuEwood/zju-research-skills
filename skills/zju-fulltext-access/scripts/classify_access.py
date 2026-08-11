#!/usr/bin/env python3
"""Classify likely lawful full-text routes from local metadata; no network access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"password", "passwd", "cookie", "token", "secret", "credential", "session"}


def has_sensitive_data(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).casefold() in SENSITIVE_KEYS and item not in (None, "", False):
                findings.append(child)
            findings.extend(has_sensitive_data(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(has_sensitive_data(item, f"{path}[{index}]"))
    return findings


def classify(record: dict[str, Any]) -> dict[str, Any]:
    sensitive = has_sensitive_data(record)
    if sensitive:
        return {"status": "blocked_sensitive_input", "route": "remove_credentials", "sensitive_fields": sensitive}
    oa_url = record.get("oa_url") or record.get("repository_url") or record.get("public_fulltext_url")
    api_url = record.get("api_url")
    database = record.get("database") or record.get("publisher")
    doi = record.get("doi")
    if oa_url:
        route, status, url = "open_access", "likely", oa_url
    elif api_url and record.get("api_access") == "public":
        route, status, url = "official_public_api", "likely", api_url
    elif database:
        route, status, url = "zju_library_interactive", "subscription_required", "https://libweb.zju.edu.cn/56334/list.htm"
    elif doi:
        route, status, url = "manual_identifier_lookup", "unresolved", f"https://doi.org/{str(doi).removeprefix('https://doi.org/')}"
    else:
        route, status, url = "manual_citation_resolution", "unresolved", ""
    return {
        "status": status,
        "route": route,
        "url": url,
        "requires_interactive_authentication": route == "zju_library_interactive",
        "automation_allowed": route in {"open_access", "official_public_api"},
        "note": "Classification is not proof of access rights; verify on the target page.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    records = data if isinstance(data, list) else [data]
    result = [{"target": item.get("doi") or item.get("title") or str(index), **classify(item)} for index, item in enumerate(records, 1)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"classified": len(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
