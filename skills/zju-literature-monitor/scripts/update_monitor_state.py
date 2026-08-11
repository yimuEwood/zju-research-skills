#!/usr/bin/env python3
"""Classify new literature records against an offline monitor state."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"password", "passwd", "cookie", "token", "secret", "authorization"}
STATE_FIELDS = {
    "version_status": ("version_status", "publication_status", "version_type"),
    "correction_status": ("correction_status",),
    "retraction_status": ("retraction_status",),
    "access_state": ("access_state", "access_status"),
    "metadata_state": ("metadata_state", "metadata_version"),
}


def clean_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text.rstrip(".,;)")


def clean_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def clean_state(value: Any) -> Any:
    if isinstance(value, str):
        normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
        aliases = {
            "version_of_record": "version_of_record",
            "vor": "version_of_record",
            "published_version": "version_of_record",
            "online_first": "online_first",
            "early_access": "online_first",
            "pre_print": "preprint",
        }
        return aliases.get(normalized, normalized)
    if isinstance(value, (bool, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    for keys, prefix in ((("pmid", "PMID"), "pmid"), (("arxiv_id", "arxiv"), "arxiv"), (("openalex_id", "openalex"), "openalex")):
        value = str(next((record.get(key) for key in keys if record.get(key)), "")).strip().lower()
        if value:
            return f"{prefix}:{value}", False
    title = clean_title(record.get("title"))
    year = str(record.get("year") or "").strip()
    if title and year:
        return f"title-year:{title}|{year}", True
    return "", True


def semantic_state(record: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name, aliases in STATE_FIELDS.items():
        for alias in aliases:
            if record.get(alias) not in (None, "", []):
                snapshot[name] = clean_state(record[alias])
                break
    return snapshot


def work_identity(record: dict[str, Any]) -> str:
    explicit = str(record.get("work_id") or "").strip().lower()
    if explicit:
        return f"work:{explicit}"
    title = clean_title(record.get("title"))
    year = str(record.get("year") or "").strip()
    if title and year:
        return f"title-year:{title}|{year}"
    return ""


def normalize_identity_reference(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith(("doi:", "pmid:", "arxiv:", "openalex:", "title-year:")):
        prefix, raw = text.split(":", 1)
        return f"{prefix}:{clean_doi(raw) if prefix == 'doi' else raw.strip()}"
    doi = clean_doi(text)
    if re.match(r"^10\.\d{4,9}/\S+$", doi):
        return f"doi:{doi}"
    return text


def related_identities(record: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for field in ("same_work_as", "version_of", "related_identifiers"):
        value = record.get(field)
        if isinstance(value, list):
            values.extend(value)
        elif value not in (None, ""):
            values.append(value)
    return {normalized for value in values if (normalized := normalize_identity_reference(value))}


def linked_ids(record: dict[str, Any], field: str) -> list[str]:
    value = record.get(field, [])
    if not isinstance(value, list):
        value = [value]
    return sorted({str(item).strip() for item in value if str(item).strip()})


def downstream_handoff(record: dict[str, Any], status: str, record_state: dict[str, Any]) -> dict[str, Any]:
    links = {
        "claim_ids": linked_ids(record, "claim_ids"),
        "gap_ids": linked_ids(record, "gap_ids"),
        "hypothesis_ids": linked_ids(record, "hypothesis_ids"),
        "seed_record_ids": linked_ids(record, "seed_record_ids"),
    }
    retraction = str(record_state.get("retraction_status") or "").lower()
    correction = str(record_state.get("correction_status") or "").lower()
    actions: list[str] = []
    urgency = "routine"
    if retraction not in {"", "none", "false", "no", "clear", "not_retracted"}:
        urgency = "critical"
        actions.extend(["recheck_linked_claims", "reopen_evidence_synthesis", "audit_downstream_citations"])
    elif correction not in {"", "none", "false", "no", "clear"}:
        urgency = "high"
        actions.extend(["obtain_notice_and_corrected_version", "recheck_linked_claims"])
    elif status == "updated":
        urgency = "high" if links["claim_ids"] or links["hypothesis_ids"] else "important"
        actions.extend(["obtain_updated_full_text", "refresh_paper_spine"])
    elif status == "new" and (links["gap_ids"] or links["hypothesis_ids"]):
        urgency = "high"
        actions.extend(["obtain_full_text", "test_gap_or_prediction_match"])
    elif status == "new":
        actions.append("screen_for_relevance")
    return {
        "urgency": urgency,
        "linked_ids": links,
        "actions": actions,
        "next_skills": (
            ["zju-fulltext-access", "zju-paper-reader", "zju-evidence-synthesis"]
            if actions else []
        ),
    }


def state_changes(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not previous:
        # A legacy state row without a prior semantic snapshot establishes a
        # baseline; it cannot prove that a change occurred.
        return {}
    return {
        field: {"from": previous.get(field), "to": value}
        for field, value in current.items()
        if previous.get(field) != value
    }


def find_seen(
    seen: list[dict[str, Any]],
    record_identity: str,
    record_work_identity: str,
    references: set[str],
) -> dict[str, Any] | None:
    for item in seen:
        known = {str(item.get("identity") or "").lower(), *(str(value).lower() for value in item.get("aliases", []))}
        if record_identity.lower() in known:
            return item
    if references:
        for item in seen:
            known = {str(item.get("identity") or "").lower(), *(str(value).lower() for value in item.get("aliases", []))}
            if references & known:
                return item
    # A title-year key is useful for display and for identity fallback, but it is
    # not strong enough to link two different stable identifiers as versions of
    # one work. Cross-identifier updates require an explicit work_id or relation.
    if record_work_identity.startswith("work:"):
        for item in seen:
            if str(item.get("work_identity") or "").lower() == record_work_identity.lower():
                return item
    return None


def update(state: dict[str, Any], records: list[dict[str, Any]], observed_at: str) -> dict[str, Any]:
    if contains_sensitive(state) or contains_sensitive(records):
        raise ValueError("Sensitive credentials must not be stored in monitor data")
    seen = [copy.deepcopy(item) for item in state.get("seen", []) if item.get("identity")]
    classified: list[dict[str, Any]] = []
    for record in records:
        key, provisional = identity(record)
        record_state = semantic_state(record)
        record_work_identity = work_identity(record)
        references = related_identities(record)
        transition: dict[str, Any] = {}
        if not key:
            status = "unresolved_identity"
        else:
            prior = find_seen(seen, key, record_work_identity, references)
            if prior is None:
                status = "new"
                prior = {
                    "identity": key,
                    "aliases": [],
                    "work_identity": record_work_identity or None,
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                    "current_state": record_state,
                    "history": [
                        {
                            "observed_at": observed_at,
                            "change_class": "new",
                            "identity": key,
                            "state": record_state,
                        }
                    ],
                }
                seen.append(prior)
                transition = {"reason": ["identity_not_seen"], "history_preserved": True}
            else:
                previous_identity = str(prior.get("identity") or "")
                previous_state = prior.get("current_state")
                if not isinstance(previous_state, dict):
                    previous_state = semantic_state(prior)
                changes = state_changes(previous_state, record_state)
                identity_changed = key != previous_identity and key not in prior.get("aliases", [])
                status = "updated" if identity_changed or changes else "duplicate"
                if not prior.get("history"):
                    prior["history"] = [
                        {
                            "observed_at": prior.get("first_seen_at"),
                            "change_class": "prior_state",
                            "identity": previous_identity,
                            "state": previous_state,
                        }
                    ]
                if status == "updated":
                    reasons = (["linked_identity_changed"] if identity_changed else []) + [f"{field}_changed" for field in changes]
                    prior["history"].append(
                        {
                            "observed_at": observed_at,
                            "change_class": "updated",
                            "prior_identity": previous_identity,
                            "current_identity": key,
                            "changes": changes,
                            "prior_state": previous_state,
                            "current_state": {**previous_state, **record_state},
                        }
                    )
                    aliases = set(prior.get("aliases", []))
                    if previous_identity != key:
                        aliases.add(previous_identity)
                    prior["aliases"] = sorted(alias for alias in aliases if alias and alias != key)
                    prior["identity"] = key
                    prior["last_change_at"] = observed_at
                    transition = {
                        "reason": reasons,
                        "prior_identity": previous_identity,
                        "changes": changes,
                        "history_preserved": True,
                    }
                else:
                    transition = {"reason": ["no_meaningful_state_change"], "history_preserved": True}
                prior["last_seen_at"] = observed_at
                prior["work_identity"] = record_work_identity or prior.get("work_identity")
                prior["current_state"] = {**previous_state, **record_state}
        handoff = downstream_handoff(record, status, record_state)
        classified.append(
            {
                **record,
                "identity": key or None,
                "identity_provisional": provisional,
                "change_class": status,
                "state_transition": transition,
                "downstream_handoff": handoff,
            }
        )
    handoff_queue = [
        {
            "identity": item.get("identity"),
            "change_class": item["change_class"],
            **item["downstream_handoff"],
        }
        for item in classified
        if item["downstream_handoff"]["actions"]
    ]
    urgency_order = {"critical": 0, "high": 1, "important": 2, "routine": 3}
    handoff_queue.sort(key=lambda item: (urgency_order.get(item["urgency"], 9), str(item.get("identity"))))
    return {
        "profile_id": state.get("profile_id"),
        "observed_at": observed_at,
        "counts": {name: sum(item["change_class"] == name for item in classified) for name in ("new", "updated", "duplicate", "unresolved_identity")},
        "records": classified,
        "handoff_queue": handoff_queue,
        "state": {**state, "seen": sorted(seen, key=lambda item: item["identity"])},
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
