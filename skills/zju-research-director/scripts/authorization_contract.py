#!/usr/bin/env python3
"""Verify approval receipts supplied outside the mutable Research Mission.

The receipt list is a trust-boundary input owned by the caller.  This module
does not claim to verify institutional digital signatures; it prevents a
mission or agent-authored decision from approving itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from director_common import stable_hash


ALLOWED_AUTHORITY_ROLES = {
    "ethics": {
        "ethics_committee",
        "institutional_review_board",
        "animal_ethics_committee",
    },
    "privacy": {
        "data_controller",
        "data_protection_officer",
        "authorized_institutional_reviewer",
    },
    "external_action": {
        "research_owner",
        "system_owner",
        "authorized_delegate",
    },
    "patent_legal": {
        "patent_professional",
        "technology_transfer_office",
    },
    "submission_release": {
        "corresponding_author",
        "principal_investigator",
        "authorized_submitter",
    },
}

TRUSTED_SOURCE_CHANNELS = {
    "user_confirmed_input",
    "institutional_record",
    "verified_external_channel",
}

REQUIRED_FIELDS = (
    "schema_version",
    "receipt_id",
    "decision_id",
    "gate",
    "mission_id",
    "authorized_by",
    "authority_role",
    "target",
    "action",
    "scope",
    "state_sha256",
    "issued_at",
    "expires_at",
    "source_channel",
    "source_reference",
    "payload_sha256",
)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value: Any) -> datetime | None:
    if not _nonempty(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def receipt_payload(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return the exact payload covered by ``payload_sha256``."""

    return {key: value for key, value in receipt.items() if key != "payload_sha256"}


def receipt_sha256(receipt: dict[str, Any]) -> str:
    return stable_hash(receipt_payload(receipt))


def verify_trusted_receipt(
    decision: dict[str, Any],
    *,
    mission_id: str,
    gate: str,
    expected_state_sha256: str,
    trusted_receipts: Iterable[dict[str, Any]] | None,
    expected_artifact_ids: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Find and verify the separately supplied receipt referenced by a decision."""

    receipt_id = decision.get("approval_receipt_id")
    if not _nonempty(receipt_id):
        return None, ["authorization decision lacks approval_receipt_id"]
    receipts = [item for item in (trusted_receipts or []) if isinstance(item, dict)]
    matches = [item for item in receipts if item.get("receipt_id") == receipt_id]
    if not matches:
        return None, [
            "approval receipt is absent from the caller-supplied trusted receipt channel"
        ]
    if len(matches) != 1:
        return None, ["approval receipt ID is duplicated in the trusted receipt channel"]
    receipt = matches[0]
    issues: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in receipt or receipt[field] in (None, ""):
            issues.append(f"approval receipt lacks {field}")
    if receipt.get("schema_version") != "1.0":
        issues.append("approval receipt schema_version must be 1.0")
    if receipt.get("payload_sha256") != receipt_sha256(receipt):
        issues.append("approval receipt payload_sha256 does not match its canonical payload")

    exact = {
        "decision_id": decision.get("decision_id"),
        "gate": gate,
        "mission_id": mission_id,
        "authorized_by": decision.get("authorized_by"),
        "authority_role": decision.get("authority_role"),
        "target": decision.get("target"),
        "action": decision.get("action"),
        "scope": decision.get("scope"),
        "state_sha256": expected_state_sha256,
        "issued_at": decision.get("issued_at"),
        "expires_at": decision.get("expires_at"),
    }
    for field, expected in exact.items():
        if receipt.get(field) != expected:
            issues.append(f"approval receipt {field} does not match the current decision")

    role = str(receipt.get("authority_role") or "")
    if role not in ALLOWED_AUTHORITY_ROLES.get(gate, set()):
        issues.append(f"authority_role {role!r} is not allowed for {gate}")
    if receipt.get("source_channel") not in TRUSTED_SOURCE_CHANNELS:
        issues.append("approval receipt source_channel is not recognized")

    issued_at = _timestamp(receipt.get("issued_at"))
    expires_at = _timestamp(receipt.get("expires_at"))
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued_at is None:
        issues.append("approval receipt issued_at must be timezone-aware ISO-8601")
    elif issued_at > clock + timedelta(minutes=5):
        issues.append("approval receipt issued_at is in the future")
    if expires_at is None:
        issues.append("approval receipt expires_at must be timezone-aware ISO-8601")
    elif expires_at <= clock:
        issues.append("approval receipt has expired")
    if issued_at is not None and expires_at is not None and expires_at <= issued_at:
        issues.append("approval receipt expires_at must be later than issued_at")

    if gate == "submission_release":
        scoped_ids = receipt.get("scope", {}).get("artifact_ids") if isinstance(receipt.get("scope"), dict) else None
        if not isinstance(scoped_ids, list) or sorted(scoped_ids) != sorted(expected_artifact_ids or []):
            issues.append("approval receipt does not scope exactly the release artifact IDs")

    return (receipt if not issues else None), issues

