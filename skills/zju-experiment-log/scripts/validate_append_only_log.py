#!/usr/bin/env python3
"""Validate an append-only experiment-log hash chain and its corrections."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ORACLE_ID = "append_only_hash_chain_v1"


def event_hash(event: dict[str, Any]) -> str:
    material = {key: value for key, value in event.items() if key != "event_hash"}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def seal(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new correctly chained event list; useful for deterministic producers/tests."""
    sealed: list[dict[str, Any]] = []
    previous = "GENESIS"
    for source in events:
        row = dict(source)
        row["previous_event_hash"] = previous
        row["event_hash"] = event_hash(row)
        sealed.append(row)
        previous = row["event_hash"]
    return sealed


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events")
    findings: list[dict[str, str]] = []
    if not isinstance(events, list) or not events:
        return {"oracle_id": ORACLE_ID, "valid": False, "events": 0, "corrections": 0, "findings": [{"field": "events", "message": "a non-empty events list is required"}]}
    seen: dict[str, dict[str, Any]] = {}
    previous_hash = "GENESIS"
    previous_time: datetime | None = None
    corrections = 0
    for index, event in enumerate(events):
        prefix = f"events[{index}]"
        if not isinstance(event, dict):
            findings.append({"field": prefix, "message": "event must be an object"})
            continue
        event_id = str(event.get("event_id") or "")
        kind = event.get("event_type")
        if not event_id or event_id in seen:
            findings.append({"field": f"{prefix}.event_id", "message": "event_id must be unique and non-empty"})
        timestamp = _timestamp(event.get("timestamp"))
        if timestamp is None:
            findings.append({"field": f"{prefix}.timestamp", "message": "timezone-aware ISO-8601 timestamp required"})
        elif previous_time is not None and timestamp <= previous_time:
            findings.append({"field": f"{prefix}.timestamp", "message": "timestamps must increase strictly in append order"})
        if timestamp is not None:
            previous_time = timestamp
        if event.get("previous_event_hash") != previous_hash:
            findings.append({"field": f"{prefix}.previous_event_hash", "message": "hash chain does not point to the immediately preceding event"})
        computed = event_hash(event)
        if event.get("event_hash") != computed:
            findings.append({"field": f"{prefix}.event_hash", "message": "event bytes do not match event_hash"})
        if kind == "entry":
            if not isinstance(event.get("payload"), dict) or not event.get("payload"):
                findings.append({"field": f"{prefix}.payload", "message": "entry requires a non-empty payload object"})
        elif kind == "correction":
            corrections += 1
            target_id = str(event.get("supersedes_event_id") or "")
            target = seen.get(target_id)
            if target is None:
                findings.append({"field": f"{prefix}.supersedes_event_id", "message": "correction must target an earlier event"})
            elif event.get("original_event_hash") != target.get("event_hash"):
                findings.append({"field": f"{prefix}.original_event_hash", "message": "correction is not bound to the exact original event bytes"})
            if not isinstance(event.get("patch"), dict) or not event.get("patch"):
                findings.append({"field": f"{prefix}.patch", "message": "correction requires a non-empty patch object"})
            if not str(event.get("reason") or "").strip():
                findings.append({"field": f"{prefix}.reason", "message": "correction reason is required"})
            if "payload" in event:
                findings.append({"field": f"{prefix}.payload", "message": "correction must append a patch, not replace the original payload"})
        else:
            findings.append({"field": f"{prefix}.event_type", "message": "event_type must be entry or correction"})
        if event_id:
            seen[event_id] = event
        previous_hash = str(event.get("event_hash") or computed)
    return {"oracle_id": ORACLE_ID, "valid": not findings, "events": len(events), "corrections": corrections, "head_hash": previous_hash, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
