#!/usr/bin/env python3
"""Evaluation protocol v2: frozen holdouts, balanced blinding, and auditable gates.

This module is intentionally independent from the v1 runner and aggregator.  A
v1 manifest cannot be passed to the v2 aggregator, and no v1 result is imported
or relabelled as a v2 score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
DEFAULT_CONFIG_PATH = EVALS / "protocol-v2.json"
ARMS = ("no_skill", "upstream_skill", "distilled_skill")
BLIND_LABELS = ("A", "B", "C")
DEFAULT_DIMENSION_WEIGHTS = {
    "task_completeness": 0.25,
    "evidence_traceability": 0.25,
    "scientific_validity": 0.20,
    "safety_integrity": 0.20,
    "usability": 0.10,
}
CASE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{2,63}$")
CHECK_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_.-]{1,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ACTOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TRUSTED_TERMINAL_EVENT_TYPES = {"turn.completed", "response.completed"}
TERMINAL_EVENT_PATTERN = re.compile(
    r"^(?:turn|response)\.(?:completed|failed|cancelled|canceled|error)$"
)


class ProtocolError(ValueError):
    """Raised when a v2 protocol artifact is structurally invalid."""


class LegacyRunError(ProtocolError):
    """Raised when a caller attempts to relabel a pre-v2 run."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc_timestamp(value: Any, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise ProtocolError(f"{field_name} must be a valid ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtocolError(f"{field_name} must include a UTC offset")
    return parsed


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def protocol_config_sha256(config: dict[str, Any]) -> str:
    """Hash frozen protocol fields while excluding post-run evidence pointers."""

    normalized = json.loads(json.dumps(config))
    dataset = normalized.get("dataset", {})
    dataset["validated_aggregate_path"] = None
    dataset["validated_aggregate_sha256"] = None
    dataset["validated_run_dir_path"] = None
    return canonical_sha256(normalized)


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _normalized_fingerprint_value(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).split())
    if isinstance(value, list):
        return [_normalized_fingerprint_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _normalized_fingerprint_value(value[key])
            for key in sorted(value, key=str)
        }
    return value


def case_fingerprint(case: dict[str, Any]) -> str:
    """Fingerprint task substance, excluding renameable case and gold-check IDs."""

    checks = case.get("gold_checks", [])
    normalized_checks = [
        _normalized_fingerprint_value(
            {key: value for key, value in item.items() if key != "id"}
            if isinstance(item, dict)
            else item
        )
        for item in checks
    ]
    normalized_checks.sort(
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return canonical_sha256(
        {
            "prompt": _normalized_fingerprint_value(case.get("prompt")),
            "fixture": _normalized_fingerprint_value(case.get("fixture")),
            "gold_checks": normalized_checks,
        }
    )


def case_fingerprint_map(document: Any) -> dict[str, str]:
    if not isinstance(document, dict):
        raise ProtocolError("case document must be a JSON object")
    return {case["id"]: case_fingerprint(case) for case in document.get("cases", [])}


def allocation_commitment(
    secret: str,
    nonce: str,
    *,
    protocol_id: str,
    freeze_id: str,
) -> str:
    if not isinstance(secret, str) or len(secret) < 32:
        raise ProtocolError("allocation secret must contain at least 32 characters")
    if not isinstance(nonce, str) or len(nonce) < 16:
        raise ProtocolError("allocation nonce must contain at least 16 characters")
    return canonical_sha256(
        {
            "scheme": "sha256-v1",
            "protocol_id": protocol_id,
            "freeze_id": freeze_id,
            "secret": secret,
            "nonce": nonce,
        }
    )


def _allocation_seed(secret: str, nonce: str, protocol_id: str, freeze_id: str) -> int:
    commitment = allocation_commitment(
        secret,
        nonce,
        protocol_id=protocol_id,
        freeze_id=freeze_id,
    )
    return int(commitment[:16], 16)


def _resolve_repo_path(value: str | None, base_dir: Path = ROOT) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    resolved_base = base_dir.resolve()
    candidate = (path if path.is_absolute() else resolved_base / path).resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError as error:
        raise ProtocolError(f"configured path escapes the repository root: {value}") from error
    return candidate


def _safe_bundle_path(run_dir: Path, *parts: str) -> Path:
    """Resolve a run artifact and reject traversal or symlink escape."""

    resolved_root = run_dir.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ProtocolError(f"run artifact escapes the run directory: {'/'.join(parts)}") from error
    return candidate


def _validate_actor_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{field_name} must be a string")
    actor_id = value
    if not ACTOR_ID_PATTERN.fullmatch(actor_id):
        raise ProtocolError(
            f"{field_name} must match {ACTOR_ID_PATTERN.pattern} and cannot contain path separators"
        )
    return actor_id


def validate_config(config: Any) -> None:
    if not isinstance(config, dict):
        raise ProtocolError("protocol config must be a JSON object")
    if config.get("schema_version") != "2.0":
        raise ProtocolError("protocol config schema_version must be 2.0")
    if not str(config.get("protocol_id", "")).strip():
        raise ProtocolError("protocol_id is required")
    if tuple(config.get("arms", ())) != ARMS:
        raise ProtocolError(f"arms must be exactly {ARMS}")
    expected_skills = config.get("expected_skills")
    if (
        not isinstance(expected_skills, list)
        or not expected_skills
        or any(not isinstance(item, str) or not item.strip() for item in expected_skills)
        or len(set(expected_skills)) != len(expected_skills)
    ):
        raise ProtocolError("expected_skills must be a non-empty array of unique names")
    dataset = config.get("dataset")
    if not isinstance(dataset, dict) or not isinstance(dataset.get("known_case_sources"), list):
        raise ProtocolError("dataset and known_case_sources are required")
    required_dataset_fields = {
        "development_cases_path",
        "frozen_holdout_cases_path",
        "frozen_holdout_lock_path",
        "first_attempt_registry_path",
        "validated_aggregate_path",
        "validated_aggregate_sha256",
        "validated_run_dir_path",
        "precommitted_rater_ids",
        "precommitted_adjudicator_id",
        "pilot_case_ids",
        "known_case_sources",
    }
    if not required_dataset_fields.issubset(dataset):
        raise ProtocolError("dataset is missing required v2 lifecycle fields")
    for source in dataset["known_case_sources"]:
        if not isinstance(source, dict):
            raise ProtocolError("each known_case_source must be an object")
    aggregate_path = dataset.get("validated_aggregate_path")
    aggregate_hash = dataset.get("validated_aggregate_sha256")
    run_dir_path = dataset.get("validated_run_dir_path")
    precommitted_rater_ids = dataset.get("precommitted_rater_ids")
    precommitted_adjudicator_id = dataset.get("precommitted_adjudicator_id")
    release_pointer_values = (aggregate_path, aggregate_hash, run_dir_path)
    configured_release_pointer = any(value is not None for value in release_pointer_values)
    if configured_release_pointer and any(value is None for value in release_pointer_values):
        raise ProtocolError(
            "validated aggregate, hash, and raw run directory must be configured together"
        )
    if aggregate_hash is not None and not SHA256_PATTERN.fullmatch(str(aggregate_hash)):
        raise ProtocolError("validated aggregate SHA-256 is invalid")
    if configured_release_pointer:
        if not isinstance(run_dir_path, str) or not run_dir_path.strip():
            raise ProtocolError("validated raw run directory must be a non-empty path")
        if precommitted_rater_ids is None:
            raise ProtocolError("validated run requires precommitted_rater_ids")
    if precommitted_rater_ids is not None:
        if (
            not isinstance(precommitted_rater_ids, list)
            or len(precommitted_rater_ids) != 2
            or len(set(precommitted_rater_ids)) != 2
        ):
            raise ProtocolError("precommitted_rater_ids must contain exactly two distinct IDs")
        for index, rater_id in enumerate(precommitted_rater_ids):
            _validate_actor_id(rater_id, f"precommitted_rater_ids[{index}]")
        if precommitted_adjudicator_id is not None:
            adjudicator_id = _validate_actor_id(
                precommitted_adjudicator_id, "precommitted_adjudicator_id"
            )
            if adjudicator_id in set(precommitted_rater_ids):
                raise ProtocolError("precommitted adjudicator must differ from both primary raters")
    elif precommitted_adjudicator_id is not None:
        raise ProtocolError("precommitted adjudicator requires precommitted primary rater IDs")
    allocation = config.get("allocation", {})
    if allocation.get("method") != "committed_secret_skill_blocked_latin_square":
        raise ProtocolError("allocation method must be committed_secret_skill_blocked_latin_square")
    if allocation.get("commitment_scheme") != "sha256-v1":
        raise ProtocolError("allocation commitment_scheme must be sha256-v1")
    if not isinstance(allocation.get("reveal_schema_path"), str) or not allocation[
        "reveal_schema_path"
    ].strip():
        raise ProtocolError("allocation reveal_schema_path is required")
    if "seed" in allocation:
        raise ProtocolError("a public allocation seed is forbidden because it reveals the arm map")
    weights = config.get("rating", {}).get("dimension_weights", {})
    if set(weights) != set(DEFAULT_DIMENSION_WEIGHTS):
        raise ProtocolError("rating dimension names do not match the v2 rubric")
    if any(not isinstance(value, (int, float)) or value <= 0 for value in weights.values()):
        raise ProtocolError("rating dimension weights must be positive numbers")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ProtocolError("rating dimension weights must sum to 1")
    if config.get("rating", {}).get("raters_per_response") != 2:
        raise ProtocolError("v2 requires exactly two primary raters")
    denied_types = {str(item).casefold() for item in config.get("tool_policy", {}).get("deny_item_types", [])}
    if not {"web_search", "mcp_tool_call"}.issubset(denied_types):
        raise ProtocolError("tool policy must deny both web_search and mcp_tool_call")
    if config.get("tool_policy", {}).get("deny_network_commands") is not True:
        raise ProtocolError("tool policy must deny network commands")
    if config.get("tool_policy", {}).get("malformed_event_is_violation") is not True:
        raise ProtocolError("malformed events must be protocol violations")
    if config.get("tool_policy", {}).get("require_trusted_terminal_event") is not True:
        raise ProtocolError("a trusted terminal event must be required")
    rating = config.get("rating", {})
    for field in ("rubric_path", "rater_schema_path"):
        if not isinstance(rating.get(field), str) or not rating[field].strip():
            raise ProtocolError(f"rating {field} is required")
    bootstrap = config.get("bootstrap", {})
    if not isinstance(bootstrap.get("iterations"), int) or bootstrap["iterations"] < 100:
        raise ProtocolError("bootstrap iterations must be an integer >= 100")
    confidence = bootstrap.get("confidence_level")
    if not isinstance(confidence, (int, float)) or not 0 < confidence < 1:
        raise ProtocolError("bootstrap confidence_level must be between 0 and 1")
    if not isinstance(bootstrap.get("seed"), int):
        raise ProtocolError("bootstrap seed must be an integer")
    gates = config.get("release_gate", {})
    if not isinstance(gates.get("minimum_cases_per_skill"), int) or gates["minimum_cases_per_skill"] < 1:
        raise ProtocolError("release minimum_cases_per_skill must be a positive integer")
    if not isinstance(gates.get("minimum_mean_quality_gain_points"), (int, float)):
        raise ProtocolError("release minimum_mean_quality_gain_points must be numeric")
    gold_rate = gates.get("minimum_distilled_gold_check_rate")
    if not isinstance(gold_rate, (int, float)) or not 0 <= gold_rate <= 1:
        raise ProtocolError("release minimum_distilled_gold_check_rate must be between 0 and 1")
    strict_gates = (
        "require_positive_paired_ci",
        "require_frozen_unseen_holdout",
        "reject_pilot_or_known_cases",
        "require_all_gold_check_ids_exactly_once",
        "require_all_adjudications_resolved",
    )
    if any(gates.get(name) is not True for name in strict_gates):
        raise ProtocolError("all v2 strict release gates must remain enabled")
    if gates.get("maximum_distilled_critical_failures") != 0 or gates.get("maximum_protocol_deviations") != 0:
        raise ProtocolError("v2 allows no distilled critical failure or protocol deviation")


def _case_ids(document: Any) -> list[str]:
    if not isinstance(document, dict):
        raise ProtocolError("case document must be a JSON object")
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise ProtocolError("case document must contain a cases array")
    identifiers = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(identifiers) != len(cases) or any(not isinstance(item, str) for item in identifiers):
        raise ProtocolError("every case must have a string id")
    duplicates = sorted(item for item, count in Counter(identifiers).items() if count > 1)
    if duplicates:
        raise ProtocolError("duplicate case IDs: " + ", ".join(duplicates))
    return identifiers


def gold_check_ids(case: dict[str, Any]) -> list[str]:
    checks = case.get("gold_checks")
    if not isinstance(checks, list) or not checks:
        raise ProtocolError(f"case {case.get('id', '<unknown>')} must define non-empty gold_checks")
    identifiers: list[str] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ProtocolError(
                f"case {case.get('id', '<unknown>')} gold check {index} must be an object; "
                "legacy free-text checks are not valid for a v2 release"
            )
        check_id = check.get("id")
        criterion = check.get("criterion")
        if not isinstance(check_id, str) or not CHECK_ID_PATTERN.fullmatch(check_id):
            raise ProtocolError(f"case {case.get('id', '<unknown>')} has an invalid gold check id")
        if not isinstance(criterion, str) or not criterion.strip():
            raise ProtocolError(f"case {case.get('id', '<unknown>')} gold check {check_id} lacks a criterion")
        identifiers.append(check_id)
    duplicates = sorted(item for item, count in Counter(identifiers).items() if count > 1)
    if duplicates:
        raise ProtocolError(
            f"case {case.get('id', '<unknown>')} has duplicate gold check IDs: " + ", ".join(duplicates)
        )
    return identifiers


def validate_holdout_document(document: Any) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    if not isinstance(document, dict):
        return ["holdout-document-must-be-object"], []
    if document.get("schema_version") != "2.0":
        issues.append("holdout-schema-version-must-be-2.0")
    if document.get("split") != "frozen_holdout":
        issues.append("case-document-split-must-be-frozen_holdout")
    try:
        identifiers = _case_ids(document)
    except ProtocolError as error:
        return [str(error)], []
    if not identifiers:
        issues.append("frozen-holdout-is-empty")
    for case in document.get("cases", []):
        case_id = case.get("id", "<unknown>")
        if not CASE_ID_PATTERN.fullmatch(str(case_id)):
            issues.append(f"invalid-case-id:{case_id}")
        for field in ("skill", "domain", "prompt"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                issues.append(f"missing-{field}:{case_id}")
        if not isinstance(case.get("fixture"), dict):
            issues.append(f"fixture-must-be-object:{case_id}")
        if case.get("evaluation_status") != "unseen_holdout":
            issues.append(f"case-not-declared-unseen:{case_id}")
        try:
            gold_check_ids(case)
        except ProtocolError as error:
            issues.append(str(error))
    return _unique(issues), identifiers


def collect_known_case_ids(config: dict[str, Any], base_dir: Path = ROOT) -> set[str]:
    """Collect every case already exposed by development files or legacy runs."""

    known = set(config.get("dataset", {}).get("pilot_case_ids", []))
    for source in config.get("dataset", {}).get("known_case_sources", []):
        kind = source.get("kind")
        if kind == "ids":
            known.update(source.get("case_ids", []))
            continue
        path = _resolve_repo_path(source.get("path"), base_dir)
        if path is None or not path.is_file():
            raise ProtocolError(f"known-case source is missing: {source.get('path')}")
        document = read_json(path)
        if kind == "case_file":
            known.update(_case_ids(document))
        elif kind == "run_manifest":
            known.update(document.get("case_ids", []))
        else:
            raise ProtocolError(f"unsupported known-case source kind: {kind}")
    return known


def collect_known_case_fingerprints(config: dict[str, Any], base_dir: Path = ROOT) -> set[str]:
    """Collect content fingerprints so renamed known prompts cannot enter a holdout."""

    known: set[str] = set()
    for source in config.get("dataset", {}).get("known_case_sources", []):
        kind = source.get("kind")
        if kind == "ids":
            known.update(str(item) for item in source.get("case_fingerprints", []))
            continue
        path = _resolve_repo_path(source.get("path"), base_dir)
        if path is None or not path.is_file():
            raise ProtocolError(f"known-case source is missing: {source.get('path')}")
        document = read_json(path)
        if kind == "case_file":
            known.update(case_fingerprint_map(document).values())
        elif kind == "run_manifest":
            fingerprints = document.get("case_fingerprints", {}) if isinstance(document, dict) else {}
            if isinstance(fingerprints, dict):
                known.update(str(value) for value in fingerprints.values())
            elif isinstance(fingerprints, list):
                known.update(str(value) for value in fingerprints)
        else:
            raise ProtocolError(f"unsupported known-case source kind: {kind}")
    return known


def build_holdout_lock(
    cases_path: Path,
    *,
    protocol_id: str,
    freeze_id: str,
    allocation_commitment_sha256: str,
    known_case_ids: Iterable[str] = (),
    pilot_case_ids: Iterable[str] = (),
    development_case_ids: Iterable[str] = (),
    known_case_fingerprints: Iterable[str] = (),
    development_case_fingerprints: Iterable[str] = (),
    frozen_at: str | None = None,
) -> dict[str, Any]:
    """Build a lock only for a non-empty, unseen, non-overlapping holdout."""

    document = read_json(cases_path)
    issues, identifiers = validate_holdout_document(document)
    if issues:
        raise ProtocolError("holdout cannot be frozen: " + "; ".join(_unique(issues)))
    known = set(known_case_ids)
    pilot = set(pilot_case_ids)
    development = set(development_case_ids)
    fingerprints = case_fingerprint_map(document)
    known_fingerprints = set(known_case_fingerprints)
    development_fingerprints = set(development_case_fingerprints)
    issues.extend(f"pilot-case-in-holdout:{item}" for item in sorted(set(identifiers) & pilot))
    issues.extend(f"known-case-in-holdout:{item}" for item in sorted(set(identifiers) & known))
    issues.extend(f"development-case-in-holdout:{item}" for item in sorted(set(identifiers) & development))
    duplicates = sorted(
        fingerprint for fingerprint, count in Counter(fingerprints.values()).items() if count > 1
    )
    issues.extend(f"duplicate-holdout-fingerprint:{item}" for item in duplicates)
    issues.extend(
        f"known-case-fingerprint-in-holdout:{case_id}"
        for case_id, fingerprint in sorted(fingerprints.items())
        if fingerprint in known_fingerprints
    )
    issues.extend(
        f"development-case-fingerprint-in-holdout:{case_id}"
        for case_id, fingerprint in sorted(fingerprints.items())
        if fingerprint in development_fingerprints
    )
    if issues:
        raise ProtocolError("holdout cannot be frozen: " + "; ".join(_unique(issues)))
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,80}", freeze_id):
        raise ProtocolError("freeze_id must use lowercase letters, digits, dot, underscore, or hyphen")
    freeze_timestamp = frozen_at or utc_now()
    parse_utc_timestamp(freeze_timestamp, "frozen_at")
    if not isinstance(allocation_commitment_sha256, str) or not SHA256_PATTERN.fullmatch(
        allocation_commitment_sha256
    ):
        raise ProtocolError("allocation_commitment_sha256 must be a lowercase SHA-256 digest")
    attempt_genesis = canonical_sha256(
        {
            "schema_version": "2.0",
            "protocol_id": protocol_id,
            "freeze_id": freeze_id,
            "case_fingerprints": fingerprints,
            "attempts": [],
        }
    )
    return {
        "schema_version": "2.0",
        "protocol_id": protocol_id,
        "freeze_id": freeze_id,
        "split": "frozen_holdout",
        "frozen_at": freeze_timestamp,
        "sealed_before_first_run": True,
        "case_document_sha256": sha256_file(cases_path),
        "case_ids_sha256": canonical_sha256(sorted(identifiers)),
        "case_fingerprints_sha256": canonical_sha256(fingerprints),
        "case_count": len(identifiers),
        "case_ids": sorted(identifiers),
        "case_fingerprints": fingerprints,
        "allocation_commitment_sha256": allocation_commitment_sha256,
        "first_attempt_only": True,
        "attempt_registry_genesis_sha256": attempt_genesis,
        "exclusion_evidence": {
            "known_case_count_at_freeze": len(known),
            "pilot_case_count_at_freeze": len(pilot),
            "development_case_count_at_freeze": len(development),
            "overlap_count": 0,
            "known_fingerprint_count_at_freeze": len(known_fingerprints),
            "development_fingerprint_count_at_freeze": len(development_fingerprints),
            "fingerprint_overlap_count": 0,
        },
    }


def build_first_attempt_registry(
    lock: dict[str, Any],
    *,
    run_id: str,
    registered_at: str | None = None,
) -> dict[str, Any]:
    """Reserve exactly one release attempt for every fingerprint in a freeze."""

    if not isinstance(lock, dict) or lock.get("schema_version") != "2.0":
        raise ProtocolError("a valid v2 holdout lock is required")
    if lock.get("first_attempt_only") is not True:
        raise ProtocolError("holdout lock does not enforce first-attempt-only evaluation")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,100}", run_id):
        raise ProtocolError("run_id has an invalid format")
    timestamp = registered_at or utc_now()
    parse_utc_timestamp(timestamp, "registered_at")
    fingerprints = lock.get("case_fingerprints")
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise ProtocolError("holdout lock lacks case fingerprints")
    return {
        "schema_version": "2.0",
        "protocol_id": lock.get("protocol_id"),
        "freeze_id": lock.get("freeze_id"),
        "genesis_sha256": lock.get("attempt_registry_genesis_sha256"),
        "registered_at": timestamp,
        "attempts": [
            {
                "attempt_number": 1,
                "run_id": run_id,
                "case_ids": sorted(fingerprints),
                "case_fingerprints": fingerprints,
            }
        ],
    }


def audit_first_attempt_registry(
    registry_path: Path | None,
    lock: dict[str, Any],
    *,
    expected_run_id: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    if registry_path is None:
        return {"eligible": False, "issues": ["first-attempt-registry-not-configured"], "sha256": None}
    if not registry_path.is_file():
        return {"eligible": False, "issues": ["first-attempt-registry-missing"], "sha256": None}
    if registry_path.stat().st_size == 0:
        return {"eligible": False, "issues": ["first-attempt-registry-empty"], "sha256": sha256_file(registry_path)}
    try:
        registry = read_json(registry_path)
    except (OSError, json.JSONDecodeError) as error:
        return {
            "eligible": False,
            "issues": [f"first-attempt-registry-unreadable:{error}"],
            "sha256": sha256_file(registry_path),
        }
    digest = sha256_file(registry_path)
    if expected_sha256 is not None and digest != expected_sha256:
        issues.append("first-attempt-registry-hash-mismatch")
    if not isinstance(registry, dict):
        issues.append("first-attempt-registry-must-be-object")
        registry = {}
    if registry.get("schema_version") != "2.0":
        issues.append("first-attempt-registry-schema-version-mismatch")
    for field in ("protocol_id", "freeze_id"):
        if registry.get(field) != lock.get(field):
            issues.append(f"first-attempt-registry-{field.replace('_', '-')}-mismatch")
    if registry.get("genesis_sha256") != lock.get("attempt_registry_genesis_sha256"):
        issues.append("first-attempt-registry-genesis-mismatch")
    attempts = registry.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 1 or not isinstance(attempts[0], dict):
        issues.append("first-attempt-registry-must-contain-exactly-one-attempt")
        attempt: dict[str, Any] = {}
    else:
        attempt = attempts[0]
    if attempt.get("attempt_number") != 1:
        issues.append("holdout-attempt-is-not-first")
    if expected_run_id is not None and attempt.get("run_id") != expected_run_id:
        issues.append("first-attempt-registry-run-id-mismatch")
    if attempt.get("case_ids") != lock.get("case_ids"):
        issues.append("first-attempt-registry-case-ids-mismatch")
    if attempt.get("case_fingerprints") != lock.get("case_fingerprints"):
        issues.append("first-attempt-registry-case-fingerprints-mismatch")
    try:
        registered_at = parse_utc_timestamp(registry.get("registered_at"), "registered_at")
    except ProtocolError:
        issues.append("first-attempt-registry-registered-at-invalid")
    else:
        try:
            frozen_at = parse_utc_timestamp(lock.get("frozen_at"), "frozen_at")
        except ProtocolError:
            issues.append("holdout-lock-frozen-at-invalid-for-registry")
        else:
            if registered_at < frozen_at:
                issues.append("first-attempt-registry-predates-holdout-freeze")
    return {
        "eligible": not issues,
        "issues": _unique(issues),
        "sha256": digest,
        "run_id": attempt.get("run_id"),
        "registered_at": registry.get("registered_at"),
    }


def audit_holdout(
    cases_path: Path | None,
    lock_path: Path | None,
    *,
    protocol_id: str,
    known_case_ids: Iterable[str] = (),
    pilot_case_ids: Iterable[str] = (),
    development_case_ids: Iterable[str] = (),
    known_case_fingerprints: Iterable[str] = (),
    development_case_fingerprints: Iterable[str] = (),
) -> dict[str, Any]:
    issues: list[str] = []
    if cases_path is None or lock_path is None:
        return {
            "eligible": False,
            "issues": ["frozen-holdout-not-configured"],
            "case_count": 0,
            "case_ids": [],
            "lock_sha256": None,
            "case_document_sha256": None,
        }
    if not cases_path.is_file():
        issues.append("frozen-holdout-file-missing")
    if not lock_path.is_file():
        issues.append("frozen-holdout-lock-missing")
    if issues:
        return {
            "eligible": False,
            "issues": issues,
            "case_count": 0,
            "case_ids": [],
            "lock_sha256": None,
            "case_document_sha256": None,
        }
    try:
        document = read_json(cases_path)
        lock = read_json(lock_path)
    except (OSError, json.JSONDecodeError) as error:
        return {
            "eligible": False,
            "issues": [f"holdout-artifact-unreadable:{error}"],
            "case_count": 0,
            "case_ids": [],
            "lock_sha256": sha256_file(lock_path),
            "case_document_sha256": sha256_file(cases_path),
        }
    document_issues, identifiers = validate_holdout_document(document)
    issues.extend(document_issues)
    if not isinstance(lock, dict):
        issues.append("holdout-lock-must-be-object")
        lock = {}
    if lock.get("schema_version") != "2.0":
        issues.append("holdout-lock-schema-version-must-be-2.0")
    if lock.get("protocol_id") != protocol_id:
        issues.append("holdout-lock-protocol-id-mismatch")
    if lock.get("split") != "frozen_holdout":
        issues.append("holdout-lock-split-mismatch")
    if lock.get("sealed_before_first_run") is not True:
        issues.append("holdout-was-not-sealed-before-first-run")
    if lock.get("first_attempt_only") is not True:
        issues.append("holdout-lock-does-not-require-first-attempt")
    if not SHA256_PATTERN.fullmatch(str(lock.get("allocation_commitment_sha256") or "")):
        issues.append("holdout-allocation-commitment-invalid")
    if not isinstance(lock.get("freeze_id"), str) or not lock["freeze_id"].strip():
        issues.append("holdout-lock-freeze-id-missing")
    try:
        parse_utc_timestamp(lock.get("frozen_at"), "holdout lock frozen_at")
    except ProtocolError:
        issues.append("holdout-lock-frozen-at-invalid")
    if lock.get("case_document_sha256") != sha256_file(cases_path):
        issues.append("holdout-case-document-hash-mismatch")
    if lock.get("case_ids_sha256") != canonical_sha256(sorted(identifiers)):
        issues.append("holdout-case-id-hash-mismatch")
    if lock.get("case_ids") != sorted(identifiers) or lock.get("case_count") != len(identifiers):
        issues.append("holdout-lock-case-list-mismatch")
    fingerprints = case_fingerprint_map(document) if isinstance(document, dict) else {}
    if lock.get("case_fingerprints") != fingerprints:
        issues.append("holdout-lock-case-fingerprint-map-mismatch")
    if lock.get("case_fingerprints_sha256") != canonical_sha256(fingerprints):
        issues.append("holdout-case-fingerprint-hash-mismatch")
    expected_genesis = canonical_sha256(
        {
            "schema_version": "2.0",
            "protocol_id": protocol_id,
            "freeze_id": lock.get("freeze_id"),
            "case_fingerprints": fingerprints,
            "attempts": [],
        }
    )
    if lock.get("attempt_registry_genesis_sha256") != expected_genesis:
        issues.append("attempt-registry-genesis-hash-mismatch")
    known = set(known_case_ids)
    pilot = set(pilot_case_ids)
    development = set(development_case_ids)
    known_fingerprints = set(known_case_fingerprints)
    development_fingerprints = set(development_case_fingerprints)
    issues.extend(f"pilot-case-in-holdout:{item}" for item in sorted(set(identifiers) & pilot))
    issues.extend(f"known-case-in-holdout:{item}" for item in sorted(set(identifiers) & known))
    issues.extend(f"development-case-in-holdout:{item}" for item in sorted(set(identifiers) & development))
    duplicates = sorted(
        fingerprint for fingerprint, count in Counter(fingerprints.values()).items() if count > 1
    )
    issues.extend(f"duplicate-holdout-fingerprint:{item}" for item in duplicates)
    issues.extend(
        f"known-case-fingerprint-in-holdout:{case_id}"
        for case_id, fingerprint in sorted(fingerprints.items())
        if fingerprint in known_fingerprints
    )
    issues.extend(
        f"development-case-fingerprint-in-holdout:{case_id}"
        for case_id, fingerprint in sorted(fingerprints.items())
        if fingerprint in development_fingerprints
    )
    return {
        "eligible": not issues,
        "issues": _unique(issues),
        "case_count": len(identifiers),
        "case_ids": sorted(identifiers),
        "freeze_id": lock.get("freeze_id"),
        "frozen_at": lock.get("frozen_at"),
        "lock_sha256": sha256_file(lock_path),
        "case_document_sha256": sha256_file(cases_path),
        "case_fingerprints": fingerprints,
        "allocation_commitment_sha256": lock.get("allocation_commitment_sha256"),
        "attempt_registry_genesis_sha256": lock.get("attempt_registry_genesis_sha256"),
    }


def configured_holdout_audit(config: dict[str, Any], base_dir: Path = ROOT) -> dict[str, Any]:
    validate_config(config)
    dataset = config["dataset"]
    known = collect_known_case_ids(config, base_dir)
    known_fingerprints = collect_known_case_fingerprints(config, base_dir)
    development_path = _resolve_repo_path(dataset.get("development_cases_path"), base_dir)
    development_ids: set[str] = set()
    development_fingerprints: set[str] = set()
    development_issue: str | None = None
    if development_path is None or not development_path.is_file():
        development_issue = "development-case-source-missing"
    else:
        development_document = read_json(development_path)
        development_ids.update(_case_ids(development_document))
        development_fingerprints.update(case_fingerprint_map(development_document).values())
    result = audit_holdout(
        _resolve_repo_path(dataset.get("frozen_holdout_cases_path"), base_dir),
        _resolve_repo_path(dataset.get("frozen_holdout_lock_path"), base_dir),
        protocol_id=config["protocol_id"],
        known_case_ids=known,
        pilot_case_ids=dataset.get("pilot_case_ids", []),
        development_case_ids=development_ids,
        known_case_fingerprints=known_fingerprints,
        development_case_fingerprints=development_fingerprints,
    )
    if development_issue:
        result["issues"] = _unique([*result.get("issues", []), development_issue])
        result["eligible"] = False
    return result


def audit_validated_aggregate(
    config: dict[str, Any],
    holdout_audit: dict[str, Any],
    registry_audit: dict[str, Any],
    base_dir: Path = ROOT,
) -> dict[str, Any]:
    dataset = config["dataset"]
    path = _resolve_repo_path(dataset.get("validated_aggregate_path"), base_dir)
    expected_hash = dataset.get("validated_aggregate_sha256")
    issues: list[str] = []
    if holdout_audit.get("eligible") is not True:
        issues.append("validated-aggregate-holdout-not-eligible")
    if registry_audit.get("eligible") is not True:
        issues.append("validated-aggregate-first-attempt-registry-not-eligible")
    if path is None or expected_hash is None:
        issues.append("validated-aggregate-not-configured")
        return {"validated": False, "issues": _unique(issues), "sha256": None}
    if not path.is_file() or path.stat().st_size == 0:
        issues.append("validated-aggregate-missing-or-empty")
        return {"validated": False, "issues": _unique(issues), "sha256": None}
    digest = sha256_file(path)
    if digest != expected_hash:
        issues.append("validated-aggregate-hash-mismatch")
    try:
        report = read_json(path)
    except (OSError, json.JSONDecodeError) as error:
        return {"validated": False, "issues": [f"validated-aggregate-unreadable:{error}"], "sha256": digest}
    if not isinstance(report, dict):
        issues.append("validated-aggregate-must-be-object")
        report = {}
    if report.get("protocol_version") != "2.0" or report.get("protocol_id") != config["protocol_id"]:
        issues.append("validated-aggregate-protocol-mismatch")
    if report.get("protocol_config_sha256") != protocol_config_sha256(config):
        issues.append("validated-aggregate-config-hash-mismatch")
    if report.get("complete") is not True:
        issues.append("validated-aggregate-incomplete")
    if report.get("release_gate_passed") is not True:
        issues.append("validated-aggregate-release-gate-not-passed")
    evidence = report.get("evidence_integrity", {})
    if not isinstance(evidence, dict) or evidence.get("valid") is not True:
        issues.append("validated-aggregate-evidence-integrity-failed")
        if not isinstance(evidence, dict):
            evidence = {}
    report_dataset = report.get("dataset_audit", {})
    if not isinstance(report_dataset, dict) or report_dataset.get("eligible") is not True:
        issues.append("validated-aggregate-dataset-audit-not-eligible")
        if not isinstance(report_dataset, dict):
            report_dataset = {}
    if (
        report_dataset.get("lock_sha256") != holdout_audit.get("lock_sha256")
        or report_dataset.get("case_document_sha256") != holdout_audit.get("case_document_sha256")
        or report_dataset.get("case_ids") != holdout_audit.get("case_ids")
        or report_dataset.get("case_fingerprints") != holdout_audit.get("case_fingerprints")
    ):
        issues.append("validated-aggregate-holdout-binding-mismatch")
    report_registry = report.get("first_attempt_registry_audit", {})
    if not isinstance(report_registry, dict) or report_registry.get("eligible") is not True:
        issues.append("validated-aggregate-first-attempt-audit-not-eligible")
        if not isinstance(report_registry, dict):
            report_registry = {}
    if (
        report_registry.get("sha256") != registry_audit.get("sha256")
        or report_registry.get("run_id") != registry_audit.get("run_id")
    ):
        issues.append("validated-aggregate-first-attempt-binding-mismatch")
    reveal_audit = report.get("allocation_reveal_audit", {})
    if not isinstance(reveal_audit, dict) or reveal_audit.get("verified") is not True:
        issues.append("validated-aggregate-allocation-reveal-not-verified")
    if report.get("rater_ids") != dataset.get("precommitted_rater_ids"):
        issues.append("validated-aggregate-rater-binding-mismatch")
    if report.get("adjudicator_id") != dataset.get("precommitted_adjudicator_id"):
        issues.append("validated-aggregate-adjudicator-binding-mismatch")
    evidence_bindings = {
        "holdout_cases_sha256": holdout_audit.get("case_document_sha256"),
        "holdout_lock_sha256": holdout_audit.get("lock_sha256"),
        "first_attempt_registry_sha256": registry_audit.get("sha256"),
    }
    if not isinstance(evidence, dict) or any(
        evidence.get(field) != expected for field, expected in evidence_bindings.items()
    ):
        issues.append("validated-aggregate-current-evidence-binding-mismatch")

    run_dir = _resolve_repo_path(dataset.get("validated_run_dir_path"), base_dir)
    cases_path = _resolve_repo_path(dataset.get("frozen_holdout_cases_path"), base_dir)
    lock_path = _resolve_repo_path(dataset.get("frozen_holdout_lock_path"), base_dir)
    registry_path = _resolve_repo_path(dataset.get("first_attempt_registry_path"), base_dir)
    recomputed: dict[str, Any] | None = None
    if run_dir is None or not run_dir.is_dir():
        issues.append("validated-aggregate-raw-run-missing")
    elif cases_path is None or lock_path is None or registry_path is None:
        issues.append("validated-aggregate-source-artifacts-not-configured")
    else:
        try:
            recomputed = aggregate_v2(
                run_dir,
                cases_path,
                lock_path,
                registry_path,
                config,
                dataset.get("precommitted_rater_ids") or [],
                dataset.get("precommitted_adjudicator_id"),
                base_dir=base_dir,
            )
        except (OSError, json.JSONDecodeError, ProtocolError) as error:
            issues.append(f"validated-aggregate-raw-recompute-failed:{error}")
        else:
            if canonical_sha256(recomputed) != canonical_sha256(report):
                issues.append("validated-aggregate-does-not-match-raw-recomputation")
    return {
        "validated": not issues,
        "issues": _unique(issues),
        "sha256": digest,
        "path": str(path),
        "raw_recomputed": recomputed is not None,
    }


def protocol_status(config: dict[str, Any], base_dir: Path = ROOT) -> dict[str, Any]:
    validate_config(config)
    known = collect_known_case_ids(config, base_dir)
    holdout = configured_holdout_audit(config, base_dir)
    registry: dict[str, Any]
    lock_path = _resolve_repo_path(config["dataset"].get("frozen_holdout_lock_path"), base_dir)
    if holdout.get("eligible") and lock_path and lock_path.is_file():
        registry = audit_first_attempt_registry(
            _resolve_repo_path(config["dataset"].get("first_attempt_registry_path"), base_dir),
            read_json(lock_path),
        )
    else:
        registry = {"eligible": False, "issues": ["holdout-not-ready"], "sha256": None}
    aggregate = audit_validated_aggregate(config, holdout, registry, base_dir)
    rater_plan_ready = isinstance(
        config["dataset"].get("precommitted_rater_ids"), list
    )
    release_ready = bool(
        holdout.get("eligible")
        and registry.get("eligible")
        and rater_plan_ready
        and aggregate.get("validated")
    )
    return {
        "schema_version": "2.0",
        "protocol_id": config["protocol_id"],
        "status": config.get("status", "unknown"),
        "known_case_count": len(known),
        "frozen_holdout": holdout,
        "first_attempt_registry": registry,
        "validated_aggregate": aggregate,
        "holdout_ready": holdout["eligible"],
        "rater_plan_ready": rater_plan_ready,
        "execution_ready": holdout["eligible"] and registry["eligible"] and rater_plan_ready,
        "release_gate_passed": release_ready,
        "new_v2_score_available": release_ready,
        "legacy_results_excluded": config.get("legacy_results", []),
        "notice": (
            "Protocol v2 is configured but no v2 score exists until a newly frozen, unseen holdout "
            "is run under v2 and passes every gate."
        ),
    }


def _skill_seed(seed: int, skill: str) -> int:
    digest = hashlib.sha256(f"{seed}:{skill}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def balanced_arm_map(
    cases: Sequence[dict[str, Any]],
    secret: str,
    nonce: str,
    *,
    protocol_id: str,
    freeze_id: str,
) -> dict[str, dict[str, str]]:
    """Assign arms from a private committed secret using a blocked Latin design.

    Every complete three-case block places each arm exactly once at each blind
    label.  For incomplete blocks, per-label arm counts differ by at most one.
    """

    seed = _allocation_seed(secret, nonce, protocol_id, freeze_id)
    identifiers = [case.get("id") for case in cases]
    if any(not isinstance(item, str) for item in identifiers):
        raise ProtocolError("every allocation case must have a string id")
    if len(set(identifiers)) != len(identifiers):
        raise ProtocolError("allocation case IDs must be unique")
    grouped: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        skill = case.get("skill")
        if not isinstance(skill, str) or not skill.strip():
            raise ProtocolError(f"case {case.get('id')} lacks a skill")
        grouped[skill].append(case["id"])
    mapping: dict[str, dict[str, str]] = {}
    for skill in sorted(grouped):
        rng = random.Random(_skill_seed(seed, skill))
        case_ids = sorted(grouped[skill])
        rng.shuffle(case_ids)
        base = list(ARMS)
        rng.shuffle(base)
        direction = rng.choice((-1, 1))
        start = rng.randrange(3)
        for index, case_id in enumerate(case_ids):
            rotation = (start + index) % 3
            mapping[case_id] = {
                label: base[(label_index + direction * rotation) % 3]
                for label_index, label in enumerate(BLIND_LABELS)
            }
    report = allocation_balance(cases, mapping)
    if not report["valid"]:
        raise AssertionError("internal allocation error: " + "; ".join(report["issues"]))
    return mapping


def allocation_balance(
    cases: Sequence[dict[str, Any]], arm_map: dict[str, dict[str, str]]
) -> dict[str, Any]:
    by_id = {case.get("id"): case for case in cases}
    issues: list[str] = []
    if set(arm_map) != set(by_id):
        missing = sorted(set(by_id) - set(arm_map))
        extra = sorted(set(arm_map) - set(by_id))
        if missing:
            issues.append("missing-case-allocations:" + ",".join(missing))
        if extra:
            issues.append("unknown-case-allocations:" + ",".join(extra))
    skills: dict[str, Any] = {}
    grouped: dict[str, list[str]] = defaultdict(list)
    for case_id, case in by_id.items():
        grouped[str(case.get("skill"))].append(case_id)
    for skill, case_ids in sorted(grouped.items()):
        counts = {label: {arm: 0 for arm in ARMS} for label in BLIND_LABELS}
        for case_id in case_ids:
            assignment = arm_map.get(case_id, {})
            if set(assignment) != set(BLIND_LABELS) or set(assignment.values()) != set(ARMS):
                issues.append(f"invalid-label-arm-bijection:{case_id}")
                continue
            for label, arm in assignment.items():
                counts[label][arm] += 1
        maximum_imbalance = max(
            (max(per_label.values()) - min(per_label.values()) for per_label in counts.values()),
            default=0,
        )
        if maximum_imbalance > 1:
            issues.append(f"allocation-imbalance:{skill}:{maximum_imbalance}")
        skills[skill] = {
            "case_count": len(case_ids),
            "label_arm_counts": counts,
            "maximum_imbalance": maximum_imbalance,
        }
    return {"valid": not issues, "issues": issues, "skills": skills}


def verify_allocation_reveal(
    reveal: Any,
    lock: dict[str, Any],
    cases: Sequence[dict[str, Any]],
    arm_map: dict[str, dict[str, str]],
    *,
    ratings_bundle_sha256: str,
    schema_path: Path = EVALS / "allocation-reveal-v2.schema.json",
) -> dict[str, Any]:
    if not isinstance(reveal, dict):
        raise ProtocolError("allocation reveal must be a JSON object")
    if not schema_path.is_file() or not schema_path.read_bytes().strip():
        raise ProtocolError("allocation reveal schema is missing or empty")
    schema = read_json(schema_path)
    schema_errors = _json_schema_errors(reveal, schema)
    if schema_errors:
        raise ProtocolError("allocation reveal schema validation failed: " + "; ".join(schema_errors))
    for field in ("secret", "nonce", "revealed_at"):
        if not isinstance(reveal.get(field), str) or not reveal[field]:
            raise ProtocolError(f"allocation reveal lacks {field}")
    parse_utc_timestamp(reveal["revealed_at"], "allocation reveal revealed_at")
    if reveal.get("protocol_id") != lock.get("protocol_id") or reveal.get("freeze_id") != lock.get("freeze_id"):
        raise ProtocolError("allocation reveal protocol or freeze mismatch")
    commitment = allocation_commitment(
        reveal["secret"],
        reveal["nonce"],
        protocol_id=str(lock.get("protocol_id")),
        freeze_id=str(lock.get("freeze_id")),
    )
    if commitment != lock.get("allocation_commitment_sha256"):
        raise ProtocolError("allocation reveal does not open the public commitment")
    if reveal.get("commitment_sha256") != commitment:
        raise ProtocolError("allocation reveal commitment field mismatch")
    if reveal.get("ratings_bundle_sha256") != ratings_bundle_sha256:
        raise ProtocolError("allocation reveal is not bound to the completed rating bundle")
    expected = balanced_arm_map(
        cases,
        reveal["secret"],
        reveal["nonce"],
        protocol_id=str(lock.get("protocol_id")),
        freeze_id=str(lock.get("freeze_id")),
    )
    if arm_map != expected:
        raise ProtocolError("private arm map does not match the committed allocation reveal")
    return {
        "verified": True,
        "commitment_sha256": commitment,
        "ratings_bundle_sha256": ratings_bundle_sha256,
        "revealed_at": reveal["revealed_at"],
    }


def _command_segments(command: str) -> list[str]:
    lowered = command.casefold().replace("\r", "\n")
    wrapper = re.search(r"\s-(?:command|c)\s+", lowered)
    if wrapper:
        lowered = lowered[wrapper.end() :]
    return [
        segment.strip(" \t\n'\"`(){}")
        for segment in re.split(r"(?:&&|\|\||[;|\n])", lowered)
        if segment.strip(" \t\n'\"`(){}")
    ]


def _network_command_reason(command: str) -> str | None:
    for segment in _command_segments(command):
        normalized = re.sub(r"^&\s*", "", segment)
        if re.match(
            r"^(?:(?:[a-z]:)?[^\s]*[\\/])?(?:curl|wget|aria2c|scp|sftp|ssh|ftp|telnet|ping|nslookup)(?:\.exe)?\b",
            normalized,
        ):
            return "direct-network-client"
        if re.match(r"^(?:invoke-webrequest|invoke-restmethod|start-bitstransfer|test-netconnection|resolve-dnsname|iwr|irm)\b", normalized):
            return "powershell-network-command"
        if re.match(r"^git(?:\.exe)?\b.*\b(?:clone|fetch|pull|ls-remote)\b", normalized):
            return "git-network-command"
        if re.match(r"^git(?:\.exe)?\b.*\bsubmodule\s+(?:update|sync)\b", normalized):
            return "git-network-command"
        if re.match(r"^gh(?:\.exe)?\b.*\b(?:api|clone|download)\b", normalized):
            return "github-network-command"
        if re.match(r"^(?:(?:python|py)(?:\.exe)?\s+-m\s+)?pip(?:\.exe)?\s+install\b", normalized):
            return "package-manager-network-command"
        if re.match(r"^(?:npm|pnpm|yarn|bun|conda|mamba)(?:\.cmd|\.exe)?\s+(?:install|add|update|create)\b", normalized):
            return "package-manager-network-command"
        if re.search(r"\b(?:requests|httpx)\.(?:get|post|put|patch|delete|request|stream)\s*\(", normalized):
            return "embedded-python-network-call"
        if re.search(r"\burllib\.request\b|\burlopen\s*\(", normalized):
            return "embedded-python-network-call"
        if re.match(r"^(?:docker|podman)(?:\.exe)?\s+(?:pull|login)\b", normalized):
            return "container-network-command"
    return None


def scan_events_text(events_text: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or {
        "deny_item_types": ["web_search", "mcp_tool_call"],
        "deny_network_commands": True,
        "malformed_event_is_violation": True,
        "require_trusted_terminal_event": True,
    }
    denied_types = {str(item).casefold() for item in policy.get("deny_item_types", [])}
    violations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    parsed_events = 0
    malformed_lines = 0
    trusted_terminal_events = 0
    terminal_sequence: list[tuple[int, bool, str]] = []

    def record(code: str, line_number: int, item_id: str, detail: str) -> None:
        key = (code, item_id or f"line-{line_number}")
        if key in seen:
            return
        seen.add(key)
        violations.append(
            {
                "code": code,
                "line": line_number,
                "item_id": item_id or None,
                "detail": detail,
            }
        )

    for line_number, line in enumerate(events_text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            malformed_lines += 1
            if policy.get("malformed_event_is_violation", True):
                record("malformed-event", line_number, "", str(error))
            continue
        if not isinstance(event, dict):
            record("event-not-object", line_number, "", f"decoded JSON type={type(event).__name__}")
            continue
        parsed_events += 1
        top_level_type = str(event.get("type", "")).casefold()
        if TERMINAL_EVENT_PATTERN.fullmatch(top_level_type):
            terminal_id = event.get("turn_id") or event.get("response_id") or event.get("id")
            successful_terminal = (
                top_level_type in TRUSTED_TERMINAL_EVENT_TYPES
                and event.get("status") == "completed"
                and isinstance(terminal_id, str)
                and bool(terminal_id.strip())
            )
            terminal_sequence.append((line_number, successful_terminal, str(terminal_id or "")))
            if successful_terminal:
                trusted_terminal_events += 1
            else:
                record(
                    "invalid-terminal-event",
                    line_number,
                    str(terminal_id or ""),
                    "terminal event requires status=completed and a stable turn/response ID",
                )
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_id = str(item.get("id", ""))
        item_type = str(item.get("type", event.get("type", ""))).casefold()
        tool_name = str(item.get("tool_name", item.get("name", ""))).casefold()
        server = str(item.get("server", "")).casefold()
        is_web = item_type in denied_types and "web" in item_type
        is_web = is_web or "web_search" in item_type or tool_name.startswith("web.")
        is_mcp = item_type in denied_types and "mcp" in item_type
        is_mcp = (
            is_mcp
            or item_type.startswith("mcp_")
            or tool_name.startswith("mcp_")
            or "mcp__" in tool_name
            or bool(server)
        )
        if is_web:
            record("web-tool-call", line_number, item_id, item_type or tool_name)
        if is_mcp:
            record("mcp-tool-call", line_number, item_id, server or item_type or tool_name)
        if item_type == "command_execution" and policy.get("deny_network_commands", True):
            command = str(item.get("command", ""))
            reason = _network_command_reason(command)
            if reason:
                record("network-command", line_number, item_id, reason)
    if not events_text.strip():
        record("events-log-empty", 0, "", "event log contains no non-whitespace content")
    if policy.get("require_trusted_terminal_event", True) and trusted_terminal_events == 0:
        record(
            "trusted-terminal-event-missing",
            0,
            "",
            "no completed turn.completed or response.completed event with a stable ID was found",
        )
    if policy.get("require_trusted_terminal_event", True) and trusted_terminal_events > 1:
        record(
            "multiple-trusted-terminal-events",
            terminal_sequence[-1][0] if terminal_sequence else 0,
            "",
            "exactly one successful terminal event is required",
        )
    if terminal_sequence and terminal_sequence[-1][1] is not True:
        line_number, _, terminal_id = terminal_sequence[-1]
        record(
            "terminal-sequence-invalid",
            line_number,
            terminal_id,
            "the final terminal event is not a successful completed event",
        )
    return {
        "compliant": not violations,
        "parsed_event_count": parsed_events,
        "malformed_line_count": malformed_lines,
        "trusted_terminal_event_count": trusted_terminal_events,
        "protocol_deviation_count": len(violations),
        "violations": violations,
    }


def scan_events_file(path: Path, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {
            "compliant": False,
            "parsed_event_count": 0,
            "malformed_line_count": 0,
            "protocol_deviation_count": 1,
            "violations": [
                {
                    "code": "events-log-missing",
                    "line": None,
                    "item_id": None,
                    "detail": str(path),
                }
            ],
        }
    try:
        events_text = path.read_text(encoding="utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        return {
            "compliant": False,
            "parsed_event_count": 0,
            "malformed_line_count": 1,
            "trusted_terminal_event_count": 0,
            "protocol_deviation_count": 1,
            "violations": [
                {
                    "code": "events-log-invalid-utf8",
                    "line": None,
                    "item_id": None,
                    "detail": str(error),
                }
            ],
        }
    return scan_events_text(events_text, policy)


def validate_gold_submission(expected_ids: Sequence[str], submitted: Any) -> dict[str, bool]:
    if not isinstance(submitted, list):
        raise ProtocolError("gold_checks must be an array")
    by_id: dict[str, bool] = {}
    for item in submitted:
        if not isinstance(item, dict):
            raise ProtocolError("each submitted gold check must be an object")
        check_id = item.get("check_id")
        if not isinstance(check_id, str):
            raise ProtocolError("each submitted gold check must have a string check_id")
        if check_id in by_id:
            raise ProtocolError(f"duplicate submitted gold check: {check_id}")
        if not isinstance(item.get("met"), bool):
            raise ProtocolError(f"gold check {check_id} must use a boolean met value")
        if not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
            raise ProtocolError(f"gold check {check_id} must include non-empty evidence")
        by_id[check_id] = item["met"]
    expected = list(expected_ids)
    missing = sorted(set(expected) - set(by_id))
    extra = sorted(set(by_id) - set(expected))
    if missing or extra or len(by_id) != len(expected):
        parts = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if extra:
            parts.append("extra=" + ",".join(extra))
        raise ProtocolError("gold checks must match expected IDs one-to-one: " + "; ".join(parts))
    return {check_id: by_id[check_id] for check_id in expected}


def _schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _json_schema_errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the dependency-free subset used by the checked-in rater schema."""

    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_schema_type_matches(value, str(choice)) for choice in choices):
            return [f"{path}: expected type {expected_type}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value does not match const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: string is shorter than minLength")
        if schema.get("pattern") and re.fullmatch(str(schema["pattern"]), value) is None:
            errors.append(f"{path}: string does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above maximum")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: array has fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: array has more than maxItems")
        if schema.get("uniqueItems") is True:
            serialized = [canonical_sha256(item) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_json_schema_errors(item, item_schema, f"{path}[{index}]"))
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            errors.extend(f"{path}: unexpected property {key}" for key in extras)
        for key, property_schema in properties.items():
            if key in value and isinstance(property_schema, dict):
                errors.extend(_json_schema_errors(value[key], property_schema, f"{path}.{key}"))
    return errors


def execute_rater_schema(ratings: Any, schema_path: Path) -> None:
    if not schema_path.is_file() or schema_path.stat().st_size == 0:
        raise ProtocolError("rater schema is missing or empty")
    schema = read_json(schema_path)
    if not isinstance(schema, dict):
        raise ProtocolError("rater schema must be a JSON object")
    errors = _json_schema_errors(ratings, schema)
    if errors:
        raise ProtocolError("rater schema validation failed: " + "; ".join(errors[:20]))


def validate_rater_output(
    case: dict[str, Any],
    ratings: Any,
    dimension_weights: dict[str, float],
    *,
    expected_rater_id: str,
    expected_role: str,
    rubric_sha256: str,
    response_hashes: dict[str, str],
    schema_path: Path,
) -> dict[str, dict[str, Any]]:
    case_id = case["id"]
    execute_rater_schema(ratings, schema_path)
    if not isinstance(ratings, dict):
        raise ProtocolError(f"rater output for {case_id} must be an object")
    if ratings.get("schema_version") != "2.0":
        raise ProtocolError(f"rater output for {case_id} must use schema_version 2.0")
    if ratings.get("case_id") != case_id:
        raise ProtocolError(f"rater case mismatch for {case_id}")
    if ratings.get("rater_id") != expected_rater_id:
        raise ProtocolError(f"rater identity mismatch for {case_id}")
    if ratings.get("rater_role") != expected_role:
        raise ProtocolError(f"rater role mismatch for {case_id}")
    if ratings.get("rubric_sha256") != rubric_sha256:
        raise ProtocolError(f"rater output for {case_id} is not bound to the frozen rubric")
    parse_utc_timestamp(ratings.get("rated_at"), f"rater output {case_id} rated_at")
    raw_items = ratings.get("ratings")
    if not isinstance(raw_items, list):
        raise ProtocolError(f"rater output for {case_id} must contain ratings")
    labels = [item.get("blind_label") for item in raw_items if isinstance(item, dict)]
    if len(labels) != len(raw_items) or Counter(labels) != Counter(BLIND_LABELS):
        raise ProtocolError(f"rater output for {case_id} must contain A, B, and C exactly once")
    expected_gold = gold_check_ids(case)
    by_label: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        label = item["blind_label"]
        scores = item.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(dimension_weights):
            raise ProtocolError(f"rater output for {case_id}/{label} has invalid score dimensions")
        if any(not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 4 for value in scores.values()):
            raise ProtocolError(f"rater output for {case_id}/{label} has a score outside 0..4")
        if not isinstance(item.get("critical_failure"), bool):
            raise ProtocolError(f"rater output for {case_id}/{label} lacks a boolean critical_failure")
        if item.get("response_sha256") != response_hashes.get(label):
            raise ProtocolError(f"rater output for {case_id}/{label} is not bound to the rated response")
        if not isinstance(item.get("rationale"), str) or not item["rationale"].strip():
            raise ProtocolError(f"rater output for {case_id}/{label} lacks rationale")
        failure_reason = item.get("failure_reason")
        if not isinstance(failure_reason, str):
            raise ProtocolError(f"rater output for {case_id}/{label} lacks failure_reason")
        if item["critical_failure"] and not failure_reason.strip():
            raise ProtocolError(f"rater output for {case_id}/{label} must explain its critical failure")
        by_label[label] = {
            **item,
            "gold_by_id": validate_gold_submission(expected_gold, item.get("gold_checks")),
        }
    return by_label


def weighted_score(
    scores: dict[str, float], critical_failure: bool, weights: dict[str, float]
) -> float:
    if critical_failure:
        return 0.0
    return round(sum((scores[name] / 4) * weight * 100 for name, weight in weights.items()), 3)


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ProtocolError("cannot compute a quantile for an empty sequence")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction)


def paired_bootstrap_ci(
    differences: Sequence[float], *, iterations: int, confidence_level: float, seed: int
) -> dict[str, Any]:
    if not differences:
        return {
            "pairs": 0,
            "estimate": None,
            "lower": None,
            "upper": None,
            "iterations": iterations,
            "confidence_level": confidence_level,
        }
    if iterations < 100:
        raise ProtocolError("bootstrap iterations must be >= 100")
    rng = random.Random(seed)
    values = [float(item) for item in differences]
    samples = []
    for _ in range(iterations):
        samples.append(statistics.fmean(rng.choice(values) for _ in values))
    samples.sort()
    alpha = (1 - confidence_level) / 2
    return {
        "pairs": len(values),
        "estimate": round(statistics.fmean(values), 3),
        "lower": round(_quantile(samples, alpha), 3),
        "upper": round(_quantile(samples, 1 - alpha), 3),
        "iterations": iterations,
        "confidence_level": confidence_level,
        "method": "paired case bootstrap percentile interval",
    }


def quadratic_weighted_kappa(first: Sequence[int], second: Sequence[int], levels: int = 5) -> float | None:
    if len(first) != len(second):
        raise ProtocolError("agreement vectors must have equal length")
    if not first:
        return None
    denominator_scale = float((levels - 1) ** 2)
    observed = statistics.fmean(((a - b) ** 2) / denominator_scale for a, b in zip(first, second))
    first_counts = Counter(first)
    second_counts = Counter(second)
    total = len(first)
    expected = sum(
        (first_counts[i] / total)
        * (second_counts[j] / total)
        * (((i - j) ** 2) / denominator_scale)
        for i in range(levels)
        for j in range(levels)
    )
    if math.isclose(expected, 0.0):
        return 1.0 if math.isclose(observed, 0.0) else 0.0
    return round(1 - observed / expected, 6)


def unweighted_kappa(first: Sequence[bool], second: Sequence[bool]) -> float | None:
    if len(first) != len(second):
        raise ProtocolError("agreement vectors must have equal length")
    if not first:
        return None
    total = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / total
    p_first = sum(bool(item) for item in first) / total
    p_second = sum(bool(item) for item in second) / total
    expected = p_first * p_second + (1 - p_first) * (1 - p_second)
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return round((observed - expected) / (1 - expected), 6)


def agreement_report(
    dimensions: dict[str, tuple[list[int], list[int]]],
    critical: tuple[list[bool], list[bool]],
    gold: tuple[list[bool], list[bool]],
) -> dict[str, Any]:
    dimension_report = {}
    for name, (first, second) in dimensions.items():
        total = len(first)
        dimension_report[name] = {
            "ratings": total,
            "exact_agreement": round(sum(a == b for a, b in zip(first, second)) / total, 6) if total else None,
            "within_one_agreement": round(sum(abs(a - b) <= 1 for a, b in zip(first, second)) / total, 6) if total else None,
            "quadratic_weighted_kappa": quadratic_weighted_kappa(first, second),
        }
    critical_total = len(critical[0])
    gold_total = len(gold[0])
    return {
        "dimensions": dimension_report,
        "critical_failure": {
            "ratings": critical_total,
            "exact_agreement": (
                round(sum(a == b for a, b in zip(*critical)) / critical_total, 6)
                if critical_total
                else None
            ),
            "cohen_kappa": unweighted_kappa(*critical),
        },
        "gold_checks": {
            "ratings": gold_total,
            "exact_agreement": (
                round(sum(a == b for a, b in zip(*gold)) / gold_total, 6) if gold_total else None
            ),
            "cohen_kappa": unweighted_kappa(*gold),
        },
        "note": "Agreement is computed between two primary ratings before adjudication.",
    }


def assert_v2_manifest(
    manifest: Any,
    config: dict[str, Any],
    *,
    rubric_sha256: str,
    rater_schema_sha256: str,
) -> None:
    if not isinstance(manifest, dict):
        raise ProtocolError("run manifest must be a JSON object")
    version = manifest.get("protocol_version")
    if version != "2.0":
        raise LegacyRunError(
            "This run lacks an explicit protocol_version=2.0 declaration and is legacy evidence. "
            "It cannot be aggregated, migrated, or relabelled as a protocol v2 result."
        )
    if manifest.get("protocol_id") != config["protocol_id"]:
        raise ProtocolError("run manifest protocol_id does not match the v2 config")
    if manifest.get("dataset_split") != "frozen_holdout":
        raise ProtocolError("v2 release runs must use dataset_split=frozen_holdout")
    if manifest.get("protocol_config_sha256") != protocol_config_sha256(config):
        raise ProtocolError("run manifest does not bind to the exact v2 protocol configuration")
    if not COMMIT_PATTERN.fullmatch(str(manifest.get("skill_commit") or "")):
        raise ProtocolError("run manifest must bind a 40-character lowercase skill commit")
    if manifest.get("rubric_sha256") != rubric_sha256:
        raise ProtocolError("run manifest rubric hash does not match the configured rubric")
    if manifest.get("rater_schema_sha256") != rater_schema_sha256:
        raise ProtocolError("run manifest rater schema hash does not match the executed schema")


def _execution_audit(
    run_dir: Path,
    case_id: str,
    label: str,
    policy: dict[str, Any],
    *,
    skill_commit: str,
    rubric_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deviations: list[dict[str, Any]] = []
    execution_path = _safe_bundle_path(run_dir, "tasks", case_id, label, "execution.json")
    response_path = _safe_bundle_path(run_dir, "tasks", case_id, label, "response.md")
    events_path = _safe_bundle_path(run_dir, "tasks", case_id, label, "events.jsonl")
    receipt: dict[str, Any] = {
        "case_id": case_id,
        "blind_label": label,
        "execution_sha256": sha256_file(execution_path) if execution_path.is_file() else None,
        "response_sha256": sha256_file(response_path) if response_path.is_file() else None,
        "events_sha256": sha256_file(events_path) if events_path.is_file() else None,
    }
    if not execution_path.is_file():
        deviations.append({"code": "execution-record-missing", "detail": str(execution_path)})
    else:
        execution = read_json(execution_path)
        if not isinstance(execution, dict):
            deviations.append({"code": "execution-record-not-object", "detail": str(execution_path)})
            execution = {}
        if execution.get("status") != "completed" or execution.get("returncode") != 0:
            deviations.append(
                {
                    "code": "execution-not-completed",
                    "detail": f"status={execution.get('status')},returncode={execution.get('returncode')}",
                }
            )
        instruction_audit = execution.get("instruction_read_audit")
        if not isinstance(instruction_audit, dict) or instruction_audit.get("verified") is not True:
            missing = instruction_audit.get("missing", []) if isinstance(instruction_audit, dict) else []
            deviations.append({"code": "instruction-read-audit-failed", "detail": str(missing)})
        bindings = {
            "response_sha256": receipt["response_sha256"],
            "events_sha256": receipt["events_sha256"],
            "skill_commit": skill_commit,
            "rubric_sha256": rubric_sha256,
        }
        for field, expected in bindings.items():
            if execution.get(field) != expected:
                deviations.append(
                    {"code": f"execution-{field.replace('_', '-')}-mismatch", "detail": f"expected={expected}"}
                )
    if not response_path.is_file():
        deviations.append({"code": "response-missing", "detail": str(response_path)})
    else:
        try:
            response_text = response_path.read_text(encoding="utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            deviations.append({"code": "response-invalid-utf8", "detail": str(error)})
        else:
            has_content = any(
                not character.isspace()
                and unicodedata.category(character)[:1] in {"L", "N", "P", "S"}
                for character in response_text
            )
            if not has_content:
                deviations.append({"code": "response-empty", "detail": str(response_path)})
    event_audit = scan_events_file(events_path, policy)
    deviations.extend(event_audit["violations"])
    return (
        [{"case_id": case_id, "blind_label": label, **item} for item in deviations],
        receipt,
    )


def _paired_differences(rows: Sequence[dict[str, Any]], baseline: str) -> list[float]:
    by_case: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        by_case[row["case_id"]][row["arm"]] = row["quality_score"]
    return [
        arms["distilled_skill"] - arms[baseline]
        for arms in by_case.values()
        if "distilled_skill" in arms and baseline in arms
    ]


def aggregate_v2(
    run_dir: Path,
    cases_path: Path,
    lock_path: Path,
    attempt_registry_path: Path,
    config: dict[str, Any],
    rater_ids: Sequence[str],
    adjudicator_id: str | None = None,
    *,
    base_dir: Path = ROOT,
) -> dict[str, Any]:
    """Aggregate a genuine v2 run and apply release-blocking protocol gates."""

    validate_config(config)
    if len(rater_ids) != 2 or len(set(rater_ids)) != 2:
        raise ProtocolError("exactly two distinct primary rater IDs are required")
    rater_ids = [
        _validate_actor_id(rater_id, f"rater_ids[{index}]")
        for index, rater_id in enumerate(rater_ids)
    ]
    if adjudicator_id is not None:
        adjudicator_id = _validate_actor_id(adjudicator_id, "adjudicator_id")
    if adjudicator_id is not None and adjudicator_id in set(rater_ids):
        raise ProtocolError("the adjudicator must be different from both primary raters")
    if config["dataset"].get("precommitted_rater_ids") != list(rater_ids):
        raise ProtocolError("primary rater IDs do not match the precommitted protocol configuration")
    if config["dataset"].get("precommitted_adjudicator_id") != adjudicator_id:
        raise ProtocolError("adjudicator ID does not match the precommitted protocol configuration")
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise ProtocolError("raw run directory is missing")
    rubric_path = _resolve_repo_path(config["rating"]["rubric_path"], base_dir)
    rater_schema_path = _resolve_repo_path(config["rating"]["rater_schema_path"], base_dir)
    reveal_schema_path = _resolve_repo_path(config["allocation"]["reveal_schema_path"], base_dir)
    if rubric_path is None or not rubric_path.is_file() or not rubric_path.read_bytes().strip():
        raise ProtocolError("configured rubric is missing or empty")
    if rater_schema_path is None or not rater_schema_path.is_file():
        raise ProtocolError("configured rater schema is missing")
    if reveal_schema_path is None or not reveal_schema_path.is_file():
        raise ProtocolError("configured allocation reveal schema is missing")
    rubric_sha256 = sha256_file(rubric_path)
    rater_schema_sha256 = sha256_file(rater_schema_path)
    manifest_path = _safe_bundle_path(run_dir, "run-manifest.json")
    if not manifest_path.is_file() or not manifest_path.read_bytes().strip():
        raise ProtocolError("run manifest is missing or empty")
    manifest = read_json(manifest_path)
    assert_v2_manifest(
        manifest,
        config,
        rubric_sha256=rubric_sha256,
        rater_schema_sha256=rater_schema_sha256,
    )
    cases_document = read_json(cases_path)
    lock = read_json(lock_path)
    document_issues, document_case_ids = validate_holdout_document(cases_document)
    if document_issues:
        raise ProtocolError("invalid holdout case document: " + "; ".join(document_issues))
    if manifest.get("case_ids") != document_case_ids:
        raise ProtocolError("run manifest case_ids must exactly match the frozen holdout order")
    if manifest.get("case_fingerprints") != case_fingerprint_map(cases_document):
        raise ProtocolError("run manifest case fingerprints do not match the frozen holdout")
    case_by_id = {case["id"]: case for case in cases_document["cases"]}

    known_ids = collect_known_case_ids(config, base_dir)
    known_fingerprints = collect_known_case_fingerprints(config, base_dir)
    development_path = _resolve_repo_path(
        config["dataset"].get("development_cases_path"), base_dir
    )
    development_ids: set[str] = set()
    development_fingerprints: set[str] = set()
    if development_path is None or not development_path.is_file():
        raise ProtocolError("configured development case source is missing")
    development_document = read_json(development_path)
    development_ids.update(_case_ids(development_document))
    development_fingerprints.update(case_fingerprint_map(development_document).values())
    dataset_audit = audit_holdout(
        cases_path,
        lock_path,
        protocol_id=config["protocol_id"],
        known_case_ids=known_ids,
        pilot_case_ids=config["dataset"].get("pilot_case_ids", []),
        development_case_ids=development_ids,
        known_case_fingerprints=known_fingerprints,
        development_case_fingerprints=development_fingerprints,
    )
    if not dataset_audit.get("eligible"):
        raise ProtocolError("holdout audit failed: " + "; ".join(dataset_audit.get("issues", [])))
    registry_audit = audit_first_attempt_registry(
        attempt_registry_path,
        lock,
        expected_run_id=str(manifest.get("run_id") or ""),
        expected_sha256=manifest.get("first_attempt_registry_sha256"),
    )
    if not registry_audit["eligible"]:
        raise ProtocolError("first-attempt registry audit failed: " + "; ".join(registry_audit["issues"]))

    arm_payload = read_json(_safe_bundle_path(run_dir, "private", "arm-map.json"))
    if not isinstance(arm_payload, dict):
        raise ProtocolError("private arm map must be a JSON object")
    if arm_payload.get("method") != config["allocation"]["method"]:
        raise ProtocolError("private arm map allocation method does not match the v2 config")
    if "seed" in arm_payload or "secret" in arm_payload or "nonce" in arm_payload:
        raise ProtocolError("private arm map must not expose allocation seed or reveal material")
    if arm_payload.get("commitment_sha256") != lock.get("allocation_commitment_sha256"):
        raise ProtocolError("private arm map does not bind the public allocation commitment")
    arm_map = arm_payload.get("arm_map", {})
    balance = allocation_balance(cases_document["cases"], arm_map)
    if not balance["valid"]:
        raise ProtocolError("invalid arm allocation: " + "; ".join(balance["issues"]))

    if dataset_audit.get("case_ids") != sorted(document_case_ids):
        raise ProtocolError("audited holdout case IDs do not match the run cases")
    if manifest.get("holdout_lock_sha256") != dataset_audit.get("lock_sha256"):
        raise ProtocolError("run manifest holdout lock hash does not match the audited lock")
    if manifest.get("holdout_cases_sha256") != dataset_audit.get("case_document_sha256"):
        raise ProtocolError("run manifest holdout case hash does not match the audited holdout")
    if manifest.get("allocation_commitment_sha256") != lock.get("allocation_commitment_sha256"):
        raise ProtocolError("run manifest allocation commitment mismatch")
    if manifest.get("holdout_attempt") != 1:
        raise ProtocolError("a v2 release run must declare holdout_attempt=1")
    run_created_at = parse_utc_timestamp(manifest.get("created_at"), "run manifest created_at")
    frozen_at = parse_utc_timestamp(dataset_audit.get("frozen_at"), "audited frozen_at")
    if run_created_at < frozen_at:
        raise ProtocolError("the run began before the holdout was frozen")
    registry_time = parse_utc_timestamp(registry_audit.get("registered_at"), "attempt registry registered_at")
    if not frozen_at <= registry_time <= run_created_at:
        raise ProtocolError("first-attempt registry must be sealed after freeze and before run creation")

    rating_file_hashes: dict[str, str] = {}
    requested_rating_ids = list(rater_ids) + ([adjudicator_id] if adjudicator_id else [])
    for case_id in document_case_ids:
        for rater_id in requested_rating_ids:
            path = _safe_bundle_path(run_dir, "ratings", str(rater_id), f"{case_id}.json")
            if path.is_file() and path.read_bytes().strip():
                rating_file_hashes[path.relative_to(run_dir).as_posix()] = sha256_file(path)
    ratings_bundle_sha256 = canonical_sha256(rating_file_hashes)
    reveal_path = _safe_bundle_path(run_dir, "reveal", "allocation-reveal.json")
    if not reveal_path.is_file() or not reveal_path.read_bytes().strip():
        raise ProtocolError("post-rating allocation reveal is missing or empty")
    reveal_audit = verify_allocation_reveal(
        read_json(reveal_path),
        lock,
        cases_document["cases"],
        arm_map,
        ratings_bundle_sha256=ratings_bundle_sha256,
        schema_path=reveal_schema_path,
    )

    weights = config["rating"]["dimension_weights"]
    policy = config["tool_policy"]
    rows: list[dict[str, Any]] = []
    missing_ratings: list[str] = []
    rating_validation_errors: list[str] = []
    adjudication_required: list[dict[str, Any]] = []
    adjudication_resolved: list[dict[str, Any]] = []
    protocol_deviations: list[dict[str, Any]] = []
    agreement_dimensions = {name: ([], []) for name in weights}
    agreement_critical: tuple[list[bool], list[bool]] = ([], [])
    agreement_gold: tuple[list[bool], list[bool]] = ([], [])
    execution_receipts: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    rating_times: list[datetime] = []

    for case_id in document_case_ids:
        case = case_by_id[case_id]
        response_hashes: dict[str, str] = {}
        for label in BLIND_LABELS:
            deviations, receipt = _execution_audit(
                run_dir,
                case_id,
                label,
                policy,
                skill_commit=manifest["skill_commit"],
                rubric_sha256=rubric_sha256,
            )
            protocol_deviations.extend(deviations)
            execution_receipts.append(receipt)
            if receipt.get("response_sha256"):
                response_hashes[label] = receipt["response_sha256"]
        rater_outputs: list[dict[str, dict[str, Any]]] = []
        for rater_id in rater_ids:
            path = _safe_bundle_path(run_dir, "ratings", rater_id, f"{case_id}.json")
            if not path.is_file():
                missing_ratings.append(f"{rater_id}:{case_id}")
                continue
            if not path.read_bytes().strip():
                rating_validation_errors.append(f"{rater_id}:{case_id}:rating file is empty")
                continue
            try:
                document = read_json(path)
                call_id = str(document.get("call_id") or "") if isinstance(document, dict) else ""
                if call_id in seen_call_ids:
                    raise ProtocolError(f"duplicate rater call_id: {call_id}")
                seen_call_ids.add(call_id)
                rating_times.append(parse_utc_timestamp(document.get("rated_at"), "rated_at"))
                rater_outputs.append(
                    validate_rater_output(
                        case,
                        document,
                        weights,
                        expected_rater_id=rater_id,
                        expected_role="primary",
                        rubric_sha256=rubric_sha256,
                        response_hashes=response_hashes,
                        schema_path=rater_schema_path,
                    )
                )
            except (OSError, json.JSONDecodeError, ProtocolError) as error:
                rating_validation_errors.append(f"{rater_id}:{case_id}:{error}")
        if len(rater_outputs) != 2:
            continue
        adjudicator_output = None
        if adjudicator_id:
            path = _safe_bundle_path(run_dir, "ratings", adjudicator_id, f"{case_id}.json")
            if path.is_file():
                try:
                    document = read_json(path)
                    call_id = str(document.get("call_id") or "") if isinstance(document, dict) else ""
                    if call_id in seen_call_ids:
                        raise ProtocolError(f"duplicate rater call_id: {call_id}")
                    seen_call_ids.add(call_id)
                    rating_times.append(parse_utc_timestamp(document.get("rated_at"), "rated_at"))
                    adjudicator_output = validate_rater_output(
                        case,
                        document,
                        weights,
                        expected_rater_id=adjudicator_id,
                        expected_role="adjudicator",
                        rubric_sha256=rubric_sha256,
                        response_hashes=response_hashes,
                        schema_path=rater_schema_path,
                    )
                except (OSError, json.JSONDecodeError, ProtocolError) as error:
                    rating_validation_errors.append(f"{adjudicator_id}:{case_id}:{error}")
        expected_gold = gold_check_ids(case)
        for label in BLIND_LABELS:
            first = rater_outputs[0][label]
            second = rater_outputs[1][label]
            for name in weights:
                agreement_dimensions[name][0].append(first["scores"][name])
                agreement_dimensions[name][1].append(second["scores"][name])
            agreement_critical[0].append(first["critical_failure"])
            agreement_critical[1].append(second["critical_failure"])
            for check_id in expected_gold:
                agreement_gold[0].append(first["gold_by_id"][check_id])
                agreement_gold[1].append(second["gold_by_id"][check_id])
            score_disagreement = {
                name: abs(first["scores"][name] - second["scores"][name]) for name in weights
            }
            gold_disagreement = [
                check_id
                for check_id in expected_gold
                if first["gold_by_id"][check_id] != second["gold_by_id"][check_id]
            ]
            disputed = (
                any(value > 1 for value in score_disagreement.values())
                or first["critical_failure"] != second["critical_failure"]
                or bool(gold_disagreement)
            )
            dispute = {
                "case_id": case_id,
                "blind_label": label,
                "dimension_disagreement": score_disagreement,
                "critical_failure_disagreement": first["critical_failure"] != second["critical_failure"],
                "gold_check_disagreement": gold_disagreement,
            }
            if disputed and adjudicator_output is None:
                adjudication_required.append(dispute)
                scores = {
                    name: (first["scores"][name] + second["scores"][name]) / 2 for name in weights
                }
                critical = first["critical_failure"] or second["critical_failure"]
                gold_by_id = {
                    check_id: first["gold_by_id"][check_id] and second["gold_by_id"][check_id]
                    for check_id in expected_gold
                }
            elif disputed:
                third = adjudicator_output[label]
                scores = {
                    name: statistics.median(
                        [first["scores"][name], second["scores"][name], third["scores"][name]]
                    )
                    for name in weights
                }
                critical = sum(
                    bool(item["critical_failure"]) for item in (first, second, third)
                ) >= 2
                gold_by_id = {
                    check_id: sum(
                        bool(item["gold_by_id"][check_id]) for item in (first, second, third)
                    ) >= 2
                    for check_id in expected_gold
                }
                adjudication_resolved.append(
                    {
                        **dispute,
                        "adjudicator_id": adjudicator_id,
                        "method": "dimension median plus majority vote for critical and gold checks",
                    }
                )
            else:
                scores = {
                    name: (first["scores"][name] + second["scores"][name]) / 2 for name in weights
                }
                critical = first["critical_failure"] or second["critical_failure"]
                gold_by_id = dict(first["gold_by_id"])
            execution_path = _safe_bundle_path(
                run_dir, "tasks", case_id, label, "execution.json"
            )
            execution = read_json(execution_path) if execution_path.is_file() else {}
            if not isinstance(execution, dict):
                execution = {}
            usage = execution.get("usage", {})
            if not isinstance(usage, dict):
                usage = {}
            rows.append(
                {
                    "case_id": case_id,
                    "skill": case["skill"],
                    "blind_label": label,
                    "arm": arm_map[case_id][label],
                    "quality_score": weighted_score(scores, critical, weights),
                    "critical_failure": critical,
                    "gold_checks_met": sum(gold_by_id.values()),
                    "gold_checks_total": len(gold_by_id),
                    "gold_check_rate": round(sum(gold_by_id.values()) / len(gold_by_id), 6),
                    "duration_seconds": float(execution.get("duration_seconds", 0) or 0),
                    "total_tokens": int(usage.get("input_tokens", 0) or 0)
                    + int(usage.get("output_tokens", 0) or 0),
                }
            )

    if rating_times and parse_utc_timestamp(reveal_audit["revealed_at"], "revealed_at") <= max(rating_times):
        raise ProtocolError("allocation reveal must occur strictly after rating completion")

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["skill"]][row["arm"]].append(row)
    gate_config = config["release_gate"]
    bootstrap_config = config["bootstrap"]
    skill_results: dict[str, Any] = {}
    bootstrap_by_skill: dict[str, Any] = {}
    for skill in sorted(set(config.get("expected_skills", [])) | set(grouped)):
        arms = grouped.get(skill, {})
        if any(not arms.get(arm) for arm in ARMS):
            skill_results[skill] = {
                "eligible": False,
                "passed": False,
                "reasons": ["missing-arm-results"],
            }
            continue
        summaries = {}
        for arm in ARMS:
            arm_rows = arms[arm]
            gold_total = sum(row["gold_checks_total"] for row in arm_rows)
            gold_met = sum(row["gold_checks_met"] for row in arm_rows)
            summaries[arm] = {
                "cases": len(arm_rows),
                "mean_quality": round(statistics.fmean(row["quality_score"] for row in arm_rows), 3),
                "median_duration_seconds": round(statistics.median(row["duration_seconds"] for row in arm_rows), 3),
                "median_total_tokens": round(statistics.median(row["total_tokens"] for row in arm_rows), 3),
                "critical_failures": sum(bool(row["critical_failure"]) for row in arm_rows),
                "gold_checks_met": gold_met,
                "gold_checks_total": gold_total,
                "gold_check_rate": round(gold_met / gold_total, 6) if gold_total else 0.0,
            }
        stronger = max(("no_skill", "upstream_skill"), key=lambda arm: summaries[arm]["mean_quality"])
        quality_gain = round(
            summaries["distilled_skill"]["mean_quality"] - summaries[stronger]["mean_quality"], 3
        )
        differences = _paired_differences([row for arm in arms.values() for row in arm], stronger)
        ci = paired_bootstrap_ci(
            differences,
            iterations=bootstrap_config["iterations"],
            confidence_level=bootstrap_config["confidence_level"],
            seed=_skill_seed(bootstrap_config["seed"], f"{skill}:{stronger}"),
        )
        bootstrap_by_skill[skill] = {f"distilled_skill-minus-{stronger}": ci}
        skill_case_ids = {row["case_id"] for row in arms["distilled_skill"]}
        deviations_for_skill = [
            item for item in protocol_deviations if item.get("case_id") in skill_case_ids
        ]
        reasons = []
        if any(summary["cases"] < gate_config["minimum_cases_per_skill"] for summary in summaries.values()):
            reasons.append("insufficient-cases")
        if quality_gain < gate_config["minimum_mean_quality_gain_points"]:
            reasons.append("quality-gain-below-threshold")
        if gate_config.get("require_positive_paired_ci", True) and (
            ci["lower"] is None or ci["lower"] <= gate_config.get("minimum_paired_ci_lower_bound_points", 0)
        ):
            reasons.append("paired-ci-lower-bound-below-threshold")
        if summaries["distilled_skill"]["gold_check_rate"] < gate_config["minimum_distilled_gold_check_rate"]:
            reasons.append("distilled-gold-check-rate-below-threshold")
        if summaries["distilled_skill"]["critical_failures"]:
            reasons.append("distilled-critical-failure")
        if deviations_for_skill:
            reasons.append("protocol-deviation")
        if any(item["case_id"] in skill_case_ids for item in adjudication_required):
            reasons.append("adjudication-pending")
        if not dataset_audit.get("eligible"):
            reasons.append("dataset-not-release-eligible")
        skill_results[skill] = {
            "eligible": not any(reason in reasons for reason in ("insufficient-cases", "dataset-not-release-eligible")),
            "stronger_baseline": stronger,
            "quality_gain_points": quality_gain,
            "paired_bootstrap_ci": ci,
            "protocol_deviation_count": len(deviations_for_skill),
            "reasons": _unique(reasons),
            "passed": not reasons,
            "arms": summaries,
        }

    overall_bootstrap: dict[str, Any] = {}
    for baseline in ("no_skill", "upstream_skill"):
        overall_bootstrap[f"distilled_skill-minus-{baseline}"] = paired_bootstrap_ci(
            _paired_differences(rows, baseline),
            iterations=bootstrap_config["iterations"],
            confidence_level=bootstrap_config["confidence_level"],
            seed=_skill_seed(bootstrap_config["seed"], f"overall:{baseline}"),
        )
    expected_execution_count = len(document_case_ids) * len(BLIND_LABELS)
    expected_primary_rating_count = len(document_case_ids) * len(rater_ids)
    primary_rating_hashes = {
        path: digest
        for path, digest in rating_file_hashes.items()
        if any(path.startswith(f"ratings/{rater_id}/") for rater_id in rater_ids)
    }
    evidence_integrity = {
        "valid": bool(
            len(execution_receipts) == expected_execution_count
            and all(
                receipt.get("execution_sha256")
                and receipt.get("response_sha256")
                and receipt.get("events_sha256")
                for receipt in execution_receipts
            )
            and len(primary_rating_hashes) == expected_primary_rating_count
            and not missing_ratings
            and not rating_validation_errors
            and not protocol_deviations
            and dataset_audit.get("eligible")
            and registry_audit.get("eligible")
            and reveal_audit.get("verified")
        ),
        "skill_commit": manifest["skill_commit"],
        "rubric_sha256": rubric_sha256,
        "rater_schema_sha256": rater_schema_sha256,
        "allocation_reveal_schema_sha256": sha256_file(reveal_schema_path),
        "holdout_cases_sha256": sha256_file(cases_path),
        "holdout_lock_sha256": sha256_file(lock_path),
        "first_attempt_registry_sha256": sha256_file(attempt_registry_path),
        "run_manifest_sha256": sha256_file(manifest_path),
        "ratings_bundle_sha256": ratings_bundle_sha256,
        "allocation_reveal_sha256": sha256_file(reveal_path),
        "execution_artifacts": execution_receipts,
        "rating_artifacts": rating_file_hashes,
    }
    complete = (
        not missing_ratings
        and not rating_validation_errors
        and not adjudication_required
        and evidence_integrity["valid"]
    )
    expected_skills = set(config.get("expected_skills", []))
    if expected_skills and set(grouped) != expected_skills:
        complete = False
    release_passed = (
        complete
        and dataset_audit.get("eligible") is True
        and registry_audit.get("eligible") is True
        and reveal_audit.get("verified") is True
        and evidence_integrity["valid"] is True
        and not protocol_deviations
        and bool(skill_results)
        and all(result.get("passed") for result in skill_results.values())
    )
    return {
        "schema_version": "2.0",
        "protocol_version": "2.0",
        "protocol_id": config["protocol_id"],
        "protocol_config_sha256": protocol_config_sha256(config),
        "run_id": manifest.get("run_id"),
        "claim_status": "protocol_v2_result",
        "complete": complete,
        "release_gate_passed": release_passed,
        "dataset_audit": dataset_audit,
        "first_attempt_registry_audit": registry_audit,
        "allocation_reveal_audit": reveal_audit,
        "evidence_integrity": evidence_integrity,
        "allocation_balance": balance,
        "rater_ids": list(rater_ids),
        "adjudicator_id": adjudicator_id,
        "agreement": agreement_report(agreement_dimensions, agreement_critical, agreement_gold),
        "paired_bootstrap": {"overall": overall_bootstrap, "by_skill": bootstrap_by_skill},
        "missing_ratings": missing_ratings,
        "rating_validation_errors": rating_validation_errors,
        "adjudication_required": adjudication_required,
        "adjudication_resolved": adjudication_resolved,
        "protocol_deviation_count": len(protocol_deviations),
        "protocol_deviations": protocol_deviations,
        "skill_results": skill_results,
        "rows": rows,
        "legacy_results_excluded": config.get("legacy_results", []),
        "legacy_notice": (
            "No v1 aggregate, response, rating, or pilot case was imported or relabelled as v2 evidence."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")

    allocate_parser = subparsers.add_parser("allocate")
    allocate_parser.add_argument("--cases", type=Path, required=True)
    allocate_parser.add_argument("--lock", type=Path, required=True)
    allocate_parser.add_argument("--allocation-secret-file", type=Path, required=True)
    allocate_parser.add_argument("--output", type=Path, required=True)

    freeze_parser = subparsers.add_parser("freeze-holdout")
    freeze_parser.add_argument("--cases", type=Path, required=True)
    freeze_parser.add_argument("--output", type=Path, required=True)
    freeze_parser.add_argument("--freeze-id", required=True)
    freeze_parser.add_argument("--allocation-secret-file", type=Path, required=True)

    register_parser = subparsers.add_parser("register-attempt")
    register_parser.add_argument("--lock", type=Path, required=True)
    register_parser.add_argument("--run-id", required=True)
    register_parser.add_argument("--output", type=Path, required=True)

    audit_parser = subparsers.add_parser("audit-events")
    audit_parser.add_argument("--events", type=Path, required=True)

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--run-dir", type=Path, required=True)
    aggregate_parser.add_argument("--cases", type=Path, required=True)
    aggregate_parser.add_argument("--lock", type=Path, required=True)
    aggregate_parser.add_argument("--attempt-registry", type=Path, required=True)
    aggregate_parser.add_argument("--rater-id", action="append", required=True)
    aggregate_parser.add_argument("--adjudicator-id")
    aggregate_parser.add_argument("--output", type=Path)

    args = parser.parse_args()
    try:
        config = read_json(args.config)
        validate_config(config)
        if args.command == "status":
            result = protocol_status(config)
            exit_code = 0 if result["release_gate_passed"] else 2
        elif args.command == "allocate":
            cases_document = read_json(args.cases)
            lock = read_json(args.lock)
            secret_payload = read_json(args.allocation_secret_file)
            if not isinstance(lock, dict) or not isinstance(secret_payload, dict):
                raise ProtocolError("lock and allocation secret file must be JSON objects")
            commitment = allocation_commitment(
                secret_payload.get("secret"),
                secret_payload.get("nonce"),
                protocol_id=config["protocol_id"],
                freeze_id=str(lock.get("freeze_id")),
            )
            if commitment != lock.get("allocation_commitment_sha256"):
                raise ProtocolError("allocation secret file does not open the holdout commitment")
            mapping = balanced_arm_map(
                cases_document["cases"],
                secret_payload["secret"],
                secret_payload["nonce"],
                protocol_id=config["protocol_id"],
                freeze_id=lock["freeze_id"],
            )
            result = {
                "schema_version": "2.0",
                "protocol_id": config["protocol_id"],
                "method": config["allocation"]["method"],
                "commitment_sha256": commitment,
                "arm_map": mapping,
                "balance": allocation_balance(cases_document["cases"], mapping),
            }
            write_json(args.output, result)
            exit_code = 0
        elif args.command == "freeze-holdout":
            dataset = config["dataset"]
            known = collect_known_case_ids(config)
            known_fingerprints = collect_known_case_fingerprints(config)
            development_ids: set[str] = set()
            development_fingerprints: set[str] = set()
            development_path = _resolve_repo_path(dataset.get("development_cases_path"))
            if development_path is None or not development_path.is_file():
                raise ProtocolError("configured development case source is missing")
            development_document = read_json(development_path)
            development_ids.update(_case_ids(development_document))
            development_fingerprints.update(case_fingerprint_map(development_document).values())
            secret_payload = read_json(args.allocation_secret_file)
            if not isinstance(secret_payload, dict):
                raise ProtocolError("allocation secret file must be a JSON object")
            commitment = allocation_commitment(
                secret_payload.get("secret"),
                secret_payload.get("nonce"),
                protocol_id=config["protocol_id"],
                freeze_id=args.freeze_id,
            )
            result = build_holdout_lock(
                args.cases,
                protocol_id=config["protocol_id"],
                freeze_id=args.freeze_id,
                allocation_commitment_sha256=commitment,
                known_case_ids=known,
                pilot_case_ids=dataset.get("pilot_case_ids", []),
                development_case_ids=development_ids,
                known_case_fingerprints=known_fingerprints,
                development_case_fingerprints=development_fingerprints,
            )
            write_json(args.output, result)
            exit_code = 0
        elif args.command == "register-attempt":
            result = build_first_attempt_registry(
                read_json(args.lock),
                run_id=args.run_id,
            )
            write_json(args.output, result)
            exit_code = 0
        elif args.command == "audit-events":
            result = scan_events_file(args.events, config["tool_policy"])
            exit_code = 0 if result["compliant"] else 2
        else:
            result = aggregate_v2(
                args.run_dir,
                args.cases,
                args.lock,
                args.attempt_registry,
                config,
                args.rater_id,
                args.adjudicator_id,
            )
            if args.output:
                write_json(args.output, result)
            exit_code = 0 if result["release_gate_passed"] else 2
    except (OSError, json.JSONDecodeError, ProtocolError) as error:
        result = {"status": "error", "error": str(error)}
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
