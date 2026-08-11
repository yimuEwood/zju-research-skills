#!/usr/bin/env python3
"""Classify new literature records against an offline monitor state."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"password", "passwd", "cookie", "token", "secret", "authorization"}


def clean_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text.rstrip(".,;)")


def clean_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def contains_sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in SENSITIVE_KEYS or contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_sensitive(item) for item in value)
    return False


def identity(record: dict[str, Any]) -> tuple[str, bool]:
    doi = clean_doi(record.get("doi") or record.get("DOI"))
    if doi:
        return f"doi:{doi}", False
    for key, prefix in (("pmid", "pmid"), ("arxiv_id", "arxiv"), ("openalex_id", "openalex")):
        value = str(record.get(key) or "").strip().lower()
        if value:
            return f"{prefix}:{value}", False
    title = clean_title(record.get("title"))
    year = str(record.get("year") or "").strip()
    if title and year:
        return f"title-year:{title}|{year}", True
    return "", True


def update(state: dict[str, Any], records: list[dict[str, Any]], observed_at: str) -> dict[str, Any]:
    if contains_sensitive(state) or contains_sensitive(records):
        raise ValueError("Sensitive credentials must not be stored in monitor data")
    seen = {item["identity"]: item for item in state.get("seen", []) if item.get("identity")}
    classified: list[dict[str, Any]] = []
    for record in records:
        key, provisional = identity(record)
        if not key:
            status = "unresolved_identity"
        elif key not in seen:
            status = "new"
            seen[key] = {"identity": key, "first_seen_at": observed_at, "last_seen_at": observed_at}
        else:
            status = "duplicate"
            seen[key]["last_seen_at"] = observed_at
        classified.append({**record, "identity": key or None, "identity_provisional": provisional, "change_class": status})
    return {
        "profile_id": state.get("profile_id"),
        "observed_at": observed_at,
        "counts": {name: sum(item["change_class"] == name for item in classified) for name in ("new", "updated", "duplicate", "unresolved_identity")},
        "records": classified,
        "state": {**state, "seen": sorted(seen.values(), key=lambda item: item["identity"])},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON with state, records, and observed_at")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = update(payload.get("state", {}), payload.get("records", []), payload.get("observed_at", ""))
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

\n