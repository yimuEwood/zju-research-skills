#!/usr/bin/env python3
"""Operational tooling for an independently administered v3 blind evaluation.

This module prepares and verifies evidence.  It never marks an internal dry
run as independent and never fabricates L3/L4 ratings.  The independent
administrator must keep allocation secrets and Ed25519 private keys outside
the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
except ImportError:  # pragma: no cover
    Ed25519PrivateKey = Ed25519PublicKey = None
    serialization = None


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "evals/skill-evaluation-matrix-v3.json"
PROTOCOL_PATH = ROOT / "evals/portfolio-protocol-v3.json"
ARMS = ("no_skill", "strongest_open_source_baseline", "distilled_skill")
LABELS = ("A", "B", "C")
TASK_LAYERS = ("L3_controlled_task_capability", "L4_frozen_holdout_generalization")
ARTIFACT_ROLES = ("fixture", "generated_artifact", "execution_trace")


class ProtocolError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProtocolError(f"{label} must be a lowercase SHA-256")
    return value


def _require_layer(value: Any) -> str:
    if value not in TASK_LAYERS:
        raise ProtocolError(f"layer must be one of {TASK_LAYERS}")
    return str(value)


def _self_hashed(payload: dict[str, Any], field: str) -> dict[str, Any]:
    output = dict(payload)
    output[field] = canonical_sha256(payload)
    return output


def _verify_self_hash(value: dict[str, Any], field: str, label: str) -> None:
    supplied = value.get(field)
    expected = canonical_sha256({key: item for key, item in value.items() if key != field})
    if supplied != expected:
        raise ProtocolError(f"{label} self-hash is invalid")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProtocolError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ProtocolError(f"timestamp must include a time zone: {value!r}")
    return parsed


def fixture_byte_hashes(case: dict[str, Any]) -> list[str]:
    declared = case.get("fixture_files")
    if declared is None:
        return [hashlib.sha256(canonical_bytes(case.get("fixture"))).hexdigest()]
    if not isinstance(declared, list) or not declared:
        raise ProtocolError("fixture_files must be a non-empty array when supplied")
    hashes = []
    for raw_path in declared:
        path = Path(str(raw_path))
        if not path.is_file():
            raise ProtocolError(f"fixture file does not exist: {path}")
        hashes.append(sha256_file(path))
    return sorted(hashes)


def case_fingerprint(case: dict[str, Any]) -> str:
    """Fingerprint task content while ignoring rename-only case IDs."""
    prompt = " ".join(str(case.get("prompt", "")).split())
    payload = {
        "skill_id": case.get("skill_id"),
        "stratum": case.get("stratum"),
        "prompt": prompt,
        "fixture": case.get("fixture"),
        "fixture_byte_sha256s": fixture_byte_hashes(case),
        "capability_ids": sorted(case.get("capability_ids", [])),
        "gold_checks": sorted(
            case.get("gold_checks", []), key=lambda row: str(row.get("check_id", ""))
        ),
    }
    return canonical_sha256(payload)


def _matrix_maps(matrix: dict[str, Any]) -> tuple[set[str], dict[str, set[str]], dict[str, set[str]]]:
    expected: set[str] = set()
    capabilities: dict[str, set[str]] = {}
    baselines: dict[str, set[str]] = {}
    for row in matrix.get("skills", []):
        skill_id = row.get("skill_id")
        if not isinstance(skill_id, str):
            continue
        expected.add(skill_id)
        capabilities[skill_id] = {cap.get("id") for cap in row.get("capabilities", []) if isinstance(cap, dict)}
        baselines[skill_id] = set(row.get("strongest_open_source_baseline_candidates", []))
    return expected, capabilities, baselines


def validate_task_document(
    document: dict[str, Any], *, matrix: dict[str, Any] | None = None,
    protocol: dict[str, Any] | None = None, require_full_portfolio: bool = True,
    known_fingerprints: Iterable[str] = (),
) -> dict[str, Any]:
    matrix = matrix or load_json(MATRIX_PATH)
    protocol = protocol or load_json(PROTOCOL_PATH)
    layer = document.get("layer")
    if layer not in TASK_LAYERS:
        raise ProtocolError(f"layer must be one of {TASK_LAYERS}")
    layer_config = protocol["layers"][layer]
    expected_skills, capability_map, _ = _matrix_maps(matrix)
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProtocolError("task document must contain non-empty cases")
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    known = set(known_fingerprints)
    overlaps: list[str] = []
    by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fingerprint_map: dict[str, str] = {}
    fixture_hash_map: dict[str, list[str]] = {}
    for position, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ProtocolError(f"case {position} must be an object")
        required = ("case_id", "skill_id", "stratum", "prompt", "fixture", "capability_ids", "gold_checks")
        missing = [field for field in required if field not in case]
        if missing:
            raise ProtocolError(f"case {position} missing fields: {missing}")
        case_id, skill_id = case["case_id"], case["skill_id"]
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", case_id):
            raise ProtocolError(f"invalid case_id: {case_id!r}")
        if case_id in seen_ids:
            raise ProtocolError(f"duplicate case_id: {case_id}")
        seen_ids.add(case_id)
        if skill_id not in expected_skills:
            raise ProtocolError(f"unknown skill_id: {skill_id}")
        if case["stratum"] not in layer_config["required_strata"]:
            raise ProtocolError(f"{case_id}: invalid stratum {case['stratum']!r}")
        capabilities = case.get("capability_ids")
        if not isinstance(capabilities, list) or not capabilities:
            raise ProtocolError(f"{case_id}: capability_ids must be non-empty")
        unknown_caps = set(capabilities) - capability_map[skill_id]
        if unknown_caps:
            raise ProtocolError(f"{case_id}: unknown capability IDs {sorted(unknown_caps)}")
        gold = case.get("gold_checks")
        if not isinstance(gold, list) or not gold:
            raise ProtocolError(f"{case_id}: at least one gold check is required")
        gold_ids: set[str] = set()
        for check in gold:
            if not isinstance(check, dict) or not check.get("check_id") or len(str(check.get("criterion", ""))) < 10:
                raise ProtocolError(f"{case_id}: malformed gold check")
            if check["check_id"] in gold_ids:
                raise ProtocolError(f"{case_id}: duplicate gold check ID")
            gold_ids.add(check["check_id"])
        fingerprint = case_fingerprint(case)
        if fingerprint in seen_fingerprints:
            raise ProtocolError(f"duplicate task fingerprint (rename-only duplicate): {case_id}")
        seen_fingerprints.add(fingerprint)
        if fingerprint in known:
            overlaps.append(case_id)
        key = f"{skill_id}::{case_id}"
        fingerprint_map[key] = fingerprint
        fixture_hash_map[key] = fixture_byte_hashes(case)
        by_skill[skill_id].append(case)
    if overlaps:
        raise ProtocolError(f"task document overlaps known/development cases: {sorted(overlaps)}")
    if require_full_portfolio and set(by_skill) != expected_skills:
        missing = sorted(expected_skills - set(by_skill))
        extra = sorted(set(by_skill) - expected_skills)
        raise ProtocolError(f"task document must cover exactly all expected skills; missing={missing}, extra={extra}")
    for skill_id, rows in by_skill.items():
        if require_full_portfolio and len(rows) < layer_config["minimum_n_per_skill"]:
            raise ProtocolError(f"{skill_id}: insufficient cases ({len(rows)})")
        strata = Counter(row["stratum"] for row in rows)
        if require_full_portfolio:
            for stratum in layer_config["required_strata"]:
                if strata[stratum] < layer_config["minimum_per_stratum"]:
                    raise ProtocolError(f"{skill_id}: insufficient {stratum} cases")
            cap_counts = Counter(cap for row in rows for cap in row["capability_ids"])
            for capability in capability_map[skill_id]:
                if cap_counts[capability] < layer_config["minimum_cases_per_capability"]:
                    raise ProtocolError(f"{skill_id}: insufficient coverage for {capability}")
    return {
        "valid": True,
        "layer": layer,
        "case_count": len(cases),
        "skills": len(by_skill),
        "case_fingerprints": fingerprint_map,
        "fixture_byte_hashes": fixture_hash_map,
        "case_document_sha256": canonical_sha256(document),
    }


def allocation_commitment(secret: str, nonce: str, freeze_id: str, layer: str) -> str:
    if len(secret) < 32 or len(nonce) < 16:
        raise ProtocolError("allocation secret must be >=32 characters and nonce >=16 characters")
    layer = _require_layer(layer)
    return hashlib.sha256(
        (secret + "\0" + nonce + "\0" + freeze_id + "\0" + layer).encode("utf-8")
    ).hexdigest()


def build_holdout_lock(
    document: dict[str, Any], *, freeze_id: str, frozen_at: str,
    commitment_sha256: str, known_fingerprints: Iterable[str] = (),
    matrix: dict[str, Any] | None = None, protocol: dict[str, Any] | None = None,
    require_full_portfolio: bool = True,
) -> dict[str, Any]:
    _require_sha256(commitment_sha256, "allocation commitment")
    audit = validate_task_document(
        document, matrix=matrix, protocol=protocol, require_full_portfolio=require_full_portfolio,
        known_fingerprints=known_fingerprints,
    )
    parse_time(frozen_at)
    if document.get("layer") != "L4_frozen_holdout_generalization":
        raise ProtocolError("holdout lock may only freeze an L4 task document")
    genesis = canonical_sha256({
        "freeze_id": freeze_id,
        "case_document_sha256": audit["case_document_sha256"],
        "case_fingerprints": audit["case_fingerprints"],
        "fixture_byte_hashes": audit["fixture_byte_hashes"],
        "allocation_commitment_sha256": commitment_sha256,
    })
    return {
        "freeze_id": freeze_id, "layer": document["layer"],
        "frozen_at": frozen_at,
        "sealed_before_first_run": True,
        "case_document_sha256": audit["case_document_sha256"],
        "case_fingerprints": audit["case_fingerprints"],
        "fixture_byte_hashes": audit["fixture_byte_hashes"],
        "allocation_commitment_sha256": commitment_sha256,
        "attempt_registry_genesis_sha256": genesis,
        "known_fingerprint_overlap_count": 0,
    }


def build_baseline_selection(
    choices: dict[str, Any], *, selected_at: str,
    matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matrix = matrix or load_json(MATRIX_PATH)
    expected, _, candidates = _matrix_maps(matrix)
    if set(choices) != expected:
        raise ProtocolError("baseline choices must cover exactly all twenty skills")
    parse_time(selected_at)
    output: dict[str, Any] = {}
    for skill_id in sorted(expected):
        choice = choices[skill_id]
        baseline_id = choice.get("baseline_id")
        if baseline_id not in candidates[skill_id]:
            raise ProtocolError(f"{skill_id}: baseline is not a matrix candidate")
        commit = str(choice.get("source_commit", ""))
        packet_sha = str(choice.get("baseline_packet_sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", packet_sha):
            raise ProtocolError(f"{skill_id}: source commit or packet hash is malformed")
        row = {
            "baseline_id": baseline_id, "source_commit": commit, "selected_at": selected_at,
            "selection_rationale": choice.get("selection_rationale") or "Selected before execution as the strongest license-compatible candidate for this task family.",
            "selected_before_first_run": True, "baseline_packet_sha256": packet_sha,
        }
        row["selection_lock_sha256"] = canonical_sha256(row)
        output[skill_id] = row
    return output


def latin_square_assignments(
    document: dict[str, Any], *, secret: str, nonce: str, freeze_id: str,
) -> list[dict[str, str]]:
    layer = _require_layer(document.get("layer"))
    commitment = allocation_commitment(secret, nonce, freeze_id, layer)
    by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in document.get("cases", []):
        by_skill[case["skill_id"]].append(case)
    assignments: list[dict[str, str]] = []
    for skill_id, cases in sorted(by_skill.items()):
        digest = hashlib.sha256((secret + nonce + freeze_id + skill_id).encode()).digest()
        arm_order = list(ARMS)
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        rng.shuffle(arm_order)
        offset = digest[8] % 3
        ordered_cases = sorted(cases, key=lambda row: row["case_id"])
        for index, case in enumerate(ordered_cases):
            rotation = (index + offset) % 3
            for label_index, label in enumerate(LABELS):
                arm = arm_order[(label_index + rotation) % 3]
                assignments.append({
                    "layer": layer, "skill_id": skill_id, "case_id": case["case_id"], "arm": arm,
                    "blind_label": label, "allocation_commitment_sha256": commitment,
                })
        counts = Counter((row["blind_label"], row["arm"]) for row in assignments if row["skill_id"] == skill_id)
        for label in LABELS:
            values = [counts[(label, arm)] for arm in ARMS]
            if max(values) - min(values) > 1:
                raise ProtocolError(f"unbalanced Latin-square allocation for {skill_id}")
    return assignments


def build_first_attempt_registry(
    lock: dict[str, Any], *, run_id: str, registered_at: str, execution_started_at: str,
) -> dict[str, Any]:
    registered, started = parse_time(registered_at), parse_time(execution_started_at)
    frozen = parse_time(lock["frozen_at"])
    if registered < frozen or registered >= started:
        raise ProtocolError("first attempt must be registered after freeze and before execution")
    return {
        "freeze_id": lock["freeze_id"], "genesis_sha256": lock["attempt_registry_genesis_sha256"],
        "registered_at": registered_at, "registered_before_execution": True, "attempt_number": 1,
        "run_id": run_id, "case_fingerprints": lock["case_fingerprints"],
    }


def build_rater_precommit(
    primary_rater_ids: list[str], *, adjudicator_id: str, rubric_sha256: str,
) -> dict[str, Any]:
    if len(primary_rater_ids) != 2 or len(set(primary_rater_ids)) != 2:
        raise ProtocolError("exactly two distinct primary raters must be precommitted")
    if adjudicator_id in set(primary_rater_ids):
        raise ProtocolError("adjudicator must differ from both primary raters")
    if not re.fullmatch(r"[0-9a-f]{64}", rubric_sha256):
        raise ProtocolError("rubric_sha256 is malformed")
    payload = {
        "primary_rater_ids": primary_rater_ids,
        "adjudicator_id": adjudicator_id,
        "rubric_sha256": rubric_sha256,
    }
    return {**payload, "precommit_sha256": canonical_sha256(payload)}


def build_run_manifest(
    *, run_id: str, skill_commit: str, baseline_selection: dict[str, Any],
    rater_precommit: dict[str, Any], holdout_lock: dict[str, Any],
    first_attempt_registry: dict[str, Any], execution_started_at: str,
    protocol: dict[str, Any] | None = None, matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    protocol = protocol or load_json(PROTOCOL_PATH)
    matrix = matrix or load_json(MATRIX_PATH)
    started = parse_time(execution_started_at)
    registered = parse_time(first_attempt_registry["registered_at"])
    if started <= registered:
        raise ProtocolError("execution must start after first-attempt registration")
    if first_attempt_registry.get("run_id") != run_id:
        raise ProtocolError("first-attempt registry does not bind run_id")
    if not re.fullmatch(r"[0-9a-f]{40}", skill_commit):
        raise ProtocolError("skill_commit must be a full lowercase commit")
    return {
        "run_id": run_id, "skill_commit": skill_commit,
        "protocol_sha256": canonical_sha256(protocol), "capability_matrix_sha256": canonical_sha256(matrix),
        "baseline_selection_sha256": canonical_sha256(baseline_selection),
        "rater_precommit_sha256": canonical_sha256(rater_precommit),
        "holdout_lock_sha256": canonical_sha256(holdout_lock),
        "first_attempt_registry_sha256": canonical_sha256(first_attempt_registry),
        "allocation_commitment_sha256": holdout_lock["allocation_commitment_sha256"],
        "execution_started_at": execution_started_at,
    }


def build_response_manifest(
    rows: list[dict[str, Any]], *, run_id: str, layer: str,
) -> dict[str, Any]:
    layer = _require_layer(layer)
    if not isinstance(run_id, str) or not run_id:
        raise ProtocolError("response manifest run_id must be non-empty")
    seen_keys, call_ids, response_hashes = set(), set(), set()
    normalized = []
    by_case: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        if row.get("layer", layer) != layer:
            raise ProtocolError("response row layer disagrees with its manifest")
        key = (layer, row.get("skill_id"), row.get("case_id"), row.get("blind_label"))
        if key in seen_keys:
            raise ProtocolError(f"duplicate response key: {key}")
        seen_keys.add(key)
        if row.get("blind_label") not in LABELS:
            raise ProtocolError(f"invalid blind label: {row.get('blind_label')}")
        call_id = row.get("call_id")
        if not isinstance(call_id, str) or call_id in call_ids:
            raise ProtocolError("response call IDs must be non-empty and globally unique")
        call_ids.add(call_id)
        path = Path(row["response_path"])
        if not path.is_file():
            raise ProtocolError(f"response file does not exist: {path}")
        digest = sha256_file(path)
        if digest in response_hashes:
            raise ProtocolError("response hashes must differ across calls/arms; identical responses are not auditable")
        response_hashes.add(digest)
        by_case[(row["skill_id"], row["case_id"])].add(row["blind_label"])
        normalized.append({
            "layer": layer, "skill_id": row["skill_id"], "case_id": row["case_id"],
            "blind_label": row["blind_label"],
            "call_id": call_id, "model": row.get("model", "unknown"), "response_path": str(path),
            "response_sha256": digest, "response_size_bytes": path.stat().st_size,
        })
    incomplete = [key for key, labels in by_case.items() if labels != set(LABELS)]
    if incomplete:
        raise ProtocolError(f"each task needs exactly A/B/C responses: {incomplete}")
    payload = {
        "schema_version": "3.0", "run_id": run_id, "layer": layer,
        "responses": sorted(normalized, key=lambda row: (
            row["skill_id"], row["case_id"], row["blind_label"]
        )),
    }
    return _self_hashed(payload, "response_manifest_sha256")


def build_artifact_manifest(
    rows: list[dict[str, Any]], *, run_id: str, layer: str,
) -> dict[str, Any]:
    """Hash immutable fixture bytes and any generated artifacts or traces.

    Paths are retained for administrator-side revalidation.  A manifest is
    evidence only while every path still hashes to the recorded bytes.
    """
    layer = _require_layer(layer)
    if not isinstance(run_id, str) or not run_id:
        raise ProtocolError("artifact manifest run_id must be non-empty")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    fixture_cases: set[tuple[str, str]] = set()
    for position, row in enumerate(rows):
        role = row.get("artifact_role")
        if role not in ARTIFACT_ROLES:
            raise ProtocolError(f"artifact row {position}: invalid artifact_role")
        if row.get("layer", layer) != layer:
            raise ProtocolError("artifact row layer disagrees with its manifest")
        blind_label = row.get("blind_label")
        if role == "fixture" and blind_label is not None:
            raise ProtocolError("fixture artifacts are case-level and must not have a blind label")
        if role != "fixture" and blind_label not in LABELS:
            raise ProtocolError("generated artifacts and traces require an A/B/C blind label")
        path = Path(str(row.get("artifact_path", "")))
        if not path.is_file():
            raise ProtocolError(f"artifact file does not exist: {path}")
        skill_id, case_id = row.get("skill_id"), row.get("case_id")
        if not isinstance(skill_id, str) or not isinstance(case_id, str):
            raise ProtocolError("artifact skill_id and case_id must be strings")
        key = (layer, skill_id, case_id, blind_label, role, str(path))
        if key in seen:
            raise ProtocolError(f"duplicate artifact row: {key}")
        seen.add(key)
        if role == "fixture":
            fixture_cases.add((skill_id, case_id))
        normalized.append({
            "layer": layer, "skill_id": skill_id, "case_id": case_id,
            "blind_label": blind_label, "artifact_role": role,
            "artifact_path": str(path), "artifact_sha256": sha256_file(path),
            "artifact_size_bytes": path.stat().st_size,
        })
    if not normalized or not fixture_cases:
        raise ProtocolError("artifact manifest must include at least one fixture byte artifact")
    payload = {
        "schema_version": "3.0", "run_id": run_id, "layer": layer,
        "artifacts": sorted(normalized, key=lambda row: (
            row["skill_id"], row["case_id"], str(row["blind_label"]),
            row["artifact_role"], row["artifact_path"],
        )),
    }
    return _self_hashed(payload, "artifact_manifest_sha256")


def build_execution_event_manifest(
    events: list[dict[str, Any]], *, run_id: str, layer: str,
) -> dict[str, Any]:
    layer = _require_layer(layer)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, event in enumerate(events):
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ProtocolError(f"execution event {position}: event_id must be unique")
        seen.add(event_id)
        if event.get("layer", layer) != layer:
            raise ProtocolError("execution event layer disagrees with its manifest")
        payload = {
            "event_id": event_id, "run_id": run_id, "layer": layer,
            "skill_id": event.get("skill_id"), "case_id": event.get("case_id"),
            "blind_label": event.get("blind_label"), "call_id": event.get("call_id"),
            "event_type": event.get("event_type"), "occurred_at": event.get("occurred_at"),
            "tool_policy_sha256": _require_sha256(event.get("tool_policy_sha256"), "tool_policy_sha256"),
            "payload_sha256": _require_sha256(event.get("payload_sha256"), "payload_sha256"),
        }
        parse_time(str(payload["occurred_at"]))
        if payload["blind_label"] not in LABELS:
            raise ProtocolError("execution event blind_label must be A/B/C")
        if not all(isinstance(payload[field], str) and payload[field] for field in (
            "skill_id", "case_id", "call_id", "event_type"
        )):
            raise ProtocolError("execution event identity fields must be non-empty")
        normalized.append(payload)
    payload = {
        "schema_version": "3.0", "run_id": run_id, "layer": layer,
        "events": sorted(normalized, key=lambda row: (row["occurred_at"], row["event_id"])),
    }
    policies: dict[tuple[str, str], set[str]] = defaultdict(set)
    labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in normalized:
        key = (row["skill_id"], row["case_id"])
        policies[key].add(row["tool_policy_sha256"])
        labels[key].add(row["blind_label"])
    for key in policies:
        if len(policies[key]) != 1 or labels[key] != set(LABELS):
            raise ProtocolError(f"execution events do not prove A/B/C tool-policy parity for {key}")
    return _self_hashed(payload, "execution_event_manifest_sha256")


def _rating_lock_material(records: list[dict[str, Any]], *, layer: str) -> list[dict[str, Any]]:
    layer = _require_layer(layer)
    material: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for position, record in enumerate(records):
        if record.get("layer") != layer:
            continue
        key = (record.get("skill_id"), record.get("case_id"), record.get("blind_label"))
        if key in seen:
            raise ProtocolError(f"duplicate ratings-lock key: {key}")
        seen.add(key)
        ratings = record.get("primary_ratings")
        if not isinstance(ratings, list) or len(ratings) != 2:
            raise ProtocolError(f"rating row {position}: exactly two primary ratings required")
        rater_ids = [rating.get("rater_id") for rating in ratings if isinstance(rating, dict)]
        call_ids = [rating.get("call_id") for rating in ratings if isinstance(rating, dict)]
        response_sha = _require_sha256(record.get("response_sha256"), "response_sha256")
        if len(rater_ids) != 2 or len(set(rater_ids)) != 2 or len(call_ids) != 2 or len(set(call_ids)) != 2:
            raise ProtocolError("ratings lock requires two distinct raters and rating call IDs")
        if any(rating.get("response_sha256") != response_sha for rating in ratings):
            raise ProtocolError("primary ratings do not bind the locked response hash")
        adjudication = record.get("adjudication")
        if isinstance(adjudication, dict) and adjudication.get("response_sha256") != response_sha:
            raise ProtocolError("adjudication does not bind the locked response hash")
        material.append({
            "layer": layer, "skill_id": key[0], "case_id": key[1], "blind_label": key[2],
            "response_sha256": response_sha,
            "primary_ratings": sorted(ratings, key=lambda row: (
                str(row.get("rater_id")), str(row.get("call_id"))
            )),
            "adjudication": adjudication,
        })
    if not material:
        raise ProtocolError("ratings lock needs at least one rating row")
    return sorted(material, key=lambda row: (
        row["skill_id"], row["case_id"], row["blind_label"]
    ))


def build_ratings_lock(
    records: list[dict[str, Any]], *, run_id: str, layer: str, sealed_at: str,
    response_manifest_sha256: str,
) -> dict[str, Any]:
    sealed = parse_time(sealed_at)
    material = _rating_lock_material(records, layer=layer)
    for row in material:
        rating_rows = list(row["primary_ratings"])
        if isinstance(row.get("adjudication"), dict):
            rating_rows.append(row["adjudication"])
        for rating in rating_rows:
            rated = parse_time(str(rating.get("rated_at")))
            if rated > sealed:
                raise ProtocolError("a rating or adjudication was completed after the ratings lock")
    adjudications = [row for row in material if isinstance(row.get("adjudication"), dict)]
    payload = {
        "schema_version": "3.0", "run_id": run_id, "layer": _require_layer(layer),
        "sealed_at": sealed_at, "sealed_before_reveal": True,
        "response_manifest_sha256": _require_sha256(
            response_manifest_sha256, "response_manifest_sha256"
        ),
        "rating_entry_count": 2 * len(material),
        "adjudication_entry_count": len(adjudications),
        "raw_ratings_adjudication_sha256": canonical_sha256(material),
    }
    return _self_hashed(payload, "ratings_lock_sha256")


def build_rater_packet(
    document: dict[str, Any], response_manifest: dict[str, Any], *, rater_id: str,
    rubric_sha256: str,
) -> dict[str, Any]:
    layer = _require_layer(document.get("layer"))
    _verify_self_hash(response_manifest, "response_manifest_sha256", "response manifest")
    if response_manifest.get("layer") != layer:
        raise ProtocolError("response manifest layer does not match task document")
    case_index = {(row["skill_id"], row["case_id"]): row for row in document["cases"]}
    response_index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for response in response_manifest["responses"]:
        response_index[(response["skill_id"], response["case_id"])].append(response)
    tasks = []
    for key, case in sorted(case_index.items()):
        responses = sorted(response_index.get(key, []), key=lambda row: row["blind_label"])
        if [row["blind_label"] for row in responses] != list(LABELS):
            raise ProtocolError(f"missing blinded responses for {key}")
        task_surface = json.dumps({"prompt": case["prompt"], "fixture": case["fixture"]}, ensure_ascii=False)
        if key[0] in task_surface or any(arm in task_surface for arm in ARMS):
            raise ProtocolError(f"task surface leaks skill or arm identity for {key}")
        blind_case_id = "BC-" + hashlib.sha256(
            (rater_id + "\0" + layer + "\0" + key[0] + "\0" + key[1]).encode()
        ).hexdigest()[:20]
        blind_responses = []
        for row in responses:
            path = Path(row["response_path"])
            content = path.read_text(encoding="utf-8")
            if sha256_file(path) != row["response_sha256"]:
                raise ProtocolError(f"response changed after manifest creation: {key}/{row['blind_label']}")
            if key[0] in content or any(arm in content for arm in ARMS):
                raise ProtocolError(f"response leaks skill or arm identity for {key}/{row['blind_label']}")
            blind_responses.append({
                "blind_label": row["blind_label"], "response": content,
                "response_sha256": row["response_sha256"],
            })
        tasks.append({
            "blind_case_id": blind_case_id, "prompt": case["prompt"], "fixture": case["fixture"],
            "gold_checks": case["gold_checks"],
            "responses": blind_responses,
        })
    return {
        "schema_version": "3.0", "run_id": response_manifest.get("run_id"),
        "layer": layer, "rater_id": rater_id, "rubric_sha256": rubric_sha256,
        "double_blind": True, "arm_identity_present": False, "skill_identity_present": False,
        "tasks": tasks, "packet_sha256": canonical_sha256(tasks),
    }


def build_allocation_reveal(
    document: dict[str, Any], *, secret: str, nonce: str, freeze_id: str,
    ratings_completed_at: str, revealed_at: str, ratings_lock: dict[str, Any],
) -> dict[str, Any]:
    layer = _require_layer(document.get("layer"))
    _verify_self_hash(ratings_lock, "ratings_lock_sha256", "ratings lock")
    if ratings_lock.get("layer") != layer:
        raise ProtocolError("ratings lock layer does not match allocation layer")
    completed, revealed = parse_time(ratings_completed_at), parse_time(revealed_at)
    if revealed <= completed:
        raise ProtocolError("allocation reveal must occur strictly after all ratings")
    sealed = parse_time(str(ratings_lock.get("sealed_at")))
    if sealed > completed or sealed >= revealed:
        raise ProtocolError("ratings lock must be sealed no later than ratings completion and before reveal")
    assignments = latin_square_assignments(document, secret=secret, nonce=nonce, freeze_id=freeze_id)
    clean = [{key: row[key] for key in ("layer", "skill_id", "case_id", "arm", "blind_label")} for row in assignments]
    return {
        "freeze_id": freeze_id, "layer": layer, "secret": secret, "nonce": nonce,
        "allocation_commitment_sha256": allocation_commitment(secret, nonce, freeze_id, layer),
        "ratings_lock_sha256": ratings_lock["ratings_lock_sha256"],
        "ratings_completed_at": ratings_completed_at, "revealed_at": revealed_at,
        "revealed_after_all_ratings": True, "assignments": clean,
    }


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = p * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def paired_bootstrap(gains: list[float], *, iterations: int = 10000, seed: int = 20260813) -> dict[str, float]:
    if not gains:
        raise ProtocolError("paired bootstrap needs at least one case gain")
    rng = random.Random(seed)
    draws = [sum(rng.choice(gains) for _ in gains) / len(gains) for _ in range(iterations)]
    return {"mean_gain": sum(gains) / len(gains), "lower": _percentile(draws, 0.025), "upper": _percentile(draws, 0.975), "iterations": iterations}


def aggregate_three_arm(
    rating_rows: list[dict[str, Any]], reveal: dict[str, Any], *, iterations: int = 10000,
) -> dict[str, Any]:
    reveal_layer = _require_layer(reveal.get("layer"))
    assignment = {
        (row["layer"], row["skill_id"], row["case_id"], row["blind_label"]): row["arm"]
        for row in reveal["assignments"]
    }
    call_ids, response_bindings = set(), {}
    scores: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rating_rows:
        call_id = row.get("call_id")
        if not isinstance(call_id, str) or call_id in call_ids:
            raise ProtocolError("rating call IDs must be globally unique")
        call_ids.add(call_id)
        key = (row.get("layer"), row["skill_id"], row["case_id"], row["blind_label"])
        if key[0] != reveal_layer:
            raise ProtocolError("rating row layer does not match allocation reveal")
        if key not in assignment:
            raise ProtocolError(f"rating is absent from allocation reveal: {key}")
        digest = row.get("response_sha256")
        prior = response_bindings.get(digest)
        if prior is not None and prior != key:
            raise ProtocolError("response hash is reused across task arms")
        response_bindings[digest] = key
        score = row.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
            raise ProtocolError("rating scores must be in [0,100]")
        scores[(row["skill_id"], row["case_id"], assignment[key])].append(float(score))
    by_skill: dict[str, dict[str, Any]] = {}
    case_keys = sorted({(skill, case) for skill, case, _ in scores})
    for skill_id in sorted({row[0] for row in case_keys}):
        gains, arm_values = [], defaultdict(list)
        for skill, case_id in [key for key in case_keys if key[0] == skill_id]:
            means = {}
            for arm in ARMS:
                values = scores.get((skill, case_id, arm), [])
                if len(values) != 2:
                    raise ProtocolError(f"{skill}/{case_id}/{arm}: exactly two independent ratings required")
                means[arm] = sum(values) / len(values)
                arm_values[arm].append(means[arm])
            gains.append(means["distilled_skill"] - max(means["no_skill"], means["strongest_open_source_baseline"]))
        by_skill[skill_id] = {
            "n_cases": len(gains),
            "arm_means": {arm: sum(values) / len(values) for arm, values in arm_values.items()},
            "paired_gain": paired_bootstrap(gains, iterations=iterations, seed=20260813 + int(hashlib.sha256(skill_id.encode()).hexdigest()[:8], 16)),
        }
    return {
        "schema_version": "3.0", "analysis": "paired_case_bootstrap_against_stronger_control",
        "skills": by_skill, "independent_result": False,
        "claim_boundary": "Set independent_result only through a separately signed administration verification bundle; this aggregate alone is not release evidence.",
    }


def generate_ed25519_key(private_path: Path, *, key_id: str) -> dict[str, Any]:
    if Ed25519PrivateKey is None or serialization is None:
        raise ProtocolError("cryptography is required for Ed25519 operations")
    if private_path.exists():
        raise ProtocolError("refusing to overwrite an existing private key")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    private_path.write_bytes(private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        os.chmod(private_path, 0o600)
    except OSError:  # pragma: no cover - Windows ACLs are environment-specific
        pass
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return {
        "key_id": key_id, "public_key_ed25519_hex": public.hex(), "active": True,
        "registration_instruction": "An independent maintainer must add this public row to trusted-verifier-keys-v3.json and commit it before holdout freeze. Keep the private PEM outside the repository.",
    }


def sign_verification_bundle(bundle: dict[str, Any], private_path: Path) -> dict[str, Any]:
    if Ed25519PrivateKey is None or serialization is None:
        raise ProtocolError("cryptography is required for Ed25519 operations")
    if "verification_signature_ed25519" in bundle:
        raise ProtocolError("refusing to sign a bundle that already contains a signature")
    private = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise ProtocolError("private key is not Ed25519")
    signed = dict(bundle)
    signed["verification_signature_ed25519"] = private.sign(canonical_bytes(bundle)).hex()
    return signed


def _index_layer_artifacts(
    values: list[dict[str, Any]], *, self_hash_field: str, label: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise ProtocolError(f"{label} entries must be objects")
        layer = _require_layer(value.get("layer"))
        if layer in indexed:
            raise ProtocolError(f"duplicate {label} for layer {layer}")
        _verify_self_hash(value, self_hash_field, label)
        indexed[layer] = value
    if not indexed:
        raise ProtocolError(f"at least one {label} is required")
    return indexed


def _verify_manifest_files(manifest: dict[str, Any], *, collection: str, path_field: str,
                           hash_field: str, size_field: str) -> None:
    for row in manifest.get(collection, []):
        path = Path(str(row.get(path_field, "")))
        if not path.is_file():
            raise ProtocolError(f"manifest-bound file is missing: {path}")
        if sha256_file(path) != row.get(hash_field) or path.stat().st_size != row.get(size_field):
            raise ProtocolError(f"manifest-bound file changed after hashing: {path}")


def _evidence_component_hash(
    *, run_manifest: dict[str, Any], response_manifests: list[dict[str, Any]],
    artifact_manifests: list[dict[str, Any]], ratings_locks: list[dict[str, Any]],
    execution_event_manifests: list[dict[str, Any]], allocation_reveals: list[dict[str, Any]],
) -> str:
    return canonical_sha256({
        "run_manifest_sha256": canonical_sha256(run_manifest),
        "response_manifests_sha256": canonical_sha256(response_manifests),
        "artifact_manifests_sha256": canonical_sha256(artifact_manifests),
        "ratings_locks_sha256": canonical_sha256(ratings_locks),
        "execution_event_manifests_sha256": canonical_sha256(execution_event_manifests),
        "allocation_reveals_sha256": canonical_sha256(allocation_reveals),
    })


def assemble_verification_bundle(
    *, verification_id: str, verified_at: str, verifier_id: str, verification_key_id: str,
    run_manifest: dict[str, Any], holdout_lock: dict[str, Any],
    first_attempt_registry: dict[str, Any], allocation_reveals: list[dict[str, Any]],
    response_manifests: list[dict[str, Any]], artifact_manifests: list[dict[str, Any]],
    ratings_locks: list[dict[str, Any]], execution_event_manifests: list[dict[str, Any]],
    portfolio_results: dict[str, Any], administration_attestation: dict[str, Any],
    portfolio_results_file: Path | None = None,
) -> dict[str, Any]:
    parse_time(verified_at)
    run_id = run_manifest.get("run_id")
    if (
        first_attempt_registry.get("run_id") != run_id
        or administration_attestation.get("run_id") != run_id
        or portfolio_results.get("run_id") != run_id
    ):
        raise ProtocolError("verification artifacts disagree on run_id")
    if run_manifest.get("holdout_lock_sha256") != canonical_sha256(holdout_lock):
        raise ProtocolError("run manifest does not bind holdout lock")
    if run_manifest.get("first_attempt_registry_sha256") != canonical_sha256(first_attempt_registry):
        raise ProtocolError("run manifest does not bind first-attempt registry")
    responses_by_layer = _index_layer_artifacts(
        response_manifests, self_hash_field="response_manifest_sha256", label="response manifest"
    )
    artifacts_by_layer = _index_layer_artifacts(
        artifact_manifests, self_hash_field="artifact_manifest_sha256", label="artifact manifest"
    )
    locks_by_layer = _index_layer_artifacts(
        ratings_locks, self_hash_field="ratings_lock_sha256", label="ratings lock"
    )
    events_by_layer = _index_layer_artifacts(
        execution_event_manifests,
        self_hash_field="execution_event_manifest_sha256", label="execution event manifest",
    )
    reveals_by_layer: dict[str, dict[str, Any]] = {}
    for reveal in allocation_reveals:
        layer = _require_layer(reveal.get("layer"))
        if layer in reveals_by_layer:
            raise ProtocolError(f"duplicate allocation reveal for layer {layer}")
        reveals_by_layer[layer] = reveal
    expected_layers = {
        record.get("layer") for record in portfolio_results.get("records", [])
        if isinstance(record, dict) and record.get("layer") in TASK_LAYERS
    }
    if not expected_layers or not all(
        set(index) == expected_layers for index in (
            responses_by_layer, artifacts_by_layer, locks_by_layer, events_by_layer, reveals_by_layer
        )
    ):
        raise ProtocolError("task evidence components must cover the exact same L3/L4 layers as results")
    for layer in sorted(expected_layers):
        response_manifest = responses_by_layer[layer]
        artifact_manifest = artifacts_by_layer[layer]
        ratings_lock = locks_by_layer[layer]
        event_manifest = events_by_layer[layer]
        reveal = reveals_by_layer[layer]
        for component in (response_manifest, artifact_manifest, ratings_lock, event_manifest):
            if component.get("run_id") != run_id:
                raise ProtocolError("layer evidence component run_id mismatch")
        if reveal.get("ratings_lock_sha256") != ratings_lock.get("ratings_lock_sha256"):
            raise ProtocolError("allocation reveal does not bind the pre-reveal ratings lock")
        if ratings_lock.get("response_manifest_sha256") != response_manifest.get("response_manifest_sha256"):
            raise ProtocolError("ratings lock does not bind the response manifest")
        expected_commitment = allocation_commitment(
            reveal.get("secret", ""), reveal.get("nonce", ""), reveal.get("freeze_id", ""), layer
        )
        if reveal.get("allocation_commitment_sha256") != expected_commitment:
            raise ProtocolError("allocation reveal secret and nonce do not open its layer commitment")
        if layer == TASK_LAYERS[1]:
            if reveal.get("freeze_id") != holdout_lock.get("freeze_id"):
                raise ProtocolError("L4 allocation reveal freeze_id differs from holdout lock")
            if reveal.get("allocation_commitment_sha256") != holdout_lock.get("allocation_commitment_sha256"):
                raise ProtocolError("L4 allocation reveal does not open the holdout commitment")
        _verify_manifest_files(
            response_manifest, collection="responses", path_field="response_path",
            hash_field="response_sha256", size_field="response_size_bytes",
        )
        _verify_manifest_files(
            artifact_manifest, collection="artifacts", path_field="artifact_path",
            hash_field="artifact_sha256", size_field="artifact_size_bytes",
        )

        records = [
            record for record in portfolio_results.get("records", [])
            if isinstance(record, dict) and record.get("layer") == layer
        ]
        manifest_responses = {
            (row["layer"], row["skill_id"], row["case_id"], row["blind_label"]): row["response_sha256"]
            for row in response_manifest["responses"]
        }
        result_responses = {
            (record["layer"], record["skill_id"], record["case_id"], record["blind_label"]): record["response_sha256"]
            for record in records
        }
        if manifest_responses != result_responses:
            raise ProtocolError("response manifest does not exactly bind task result responses")
        material = _rating_lock_material(records, layer=layer)
        if canonical_sha256(material) != ratings_lock.get("raw_ratings_adjudication_sha256"):
            raise ProtocolError("results ratings or adjudication differ from the pre-reveal ratings lock")
        if 2 * len(material) != ratings_lock.get("rating_entry_count"):
            raise ProtocolError("ratings lock primary rating count is inconsistent")
        adjudication_count = sum(isinstance(row.get("adjudication"), dict) for row in material)
        if adjudication_count != ratings_lock.get("adjudication_entry_count"):
            raise ProtocolError("ratings lock adjudication count is inconsistent")

        fixtures: dict[tuple[str, str], list[str]] = defaultdict(list)
        generated: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for row in artifact_manifest["artifacts"]:
            if row["artifact_role"] == "fixture":
                fixtures[(row["skill_id"], row["case_id"])].append(row["artifact_sha256"])
            else:
                generated[(row["skill_id"], row["case_id"], row["blind_label"])].append(row["artifact_sha256"])
        if layer == TASK_LAYERS[1]:
            manifest_fixture_map = {
                f"{skill_id}::{case_id}": sorted(values)
                for (skill_id, case_id), values in fixtures.items()
            }
            if manifest_fixture_map != holdout_lock.get("fixture_byte_hashes"):
                raise ProtocolError("L4 artifact manifest fixture bytes differ from the frozen holdout")
        for record in records:
            fixture_hashes = sorted(fixtures.get((record["skill_id"], record["case_id"]), []))
            if not fixture_hashes or record.get("fixture_sha256") != canonical_sha256(fixture_hashes):
                raise ProtocolError("result fixture_sha256 does not bind manifest fixture bytes")
            artifact_hashes = sorted(generated.get((record["skill_id"], record["case_id"], record["blind_label"]), []))
            if sorted(record.get("artifact_sha256s", [])) != artifact_hashes:
                raise ProtocolError("result artifact_sha256s do not bind generated artifact bytes")
        event_calls = {
            (row["layer"], row["skill_id"], row["case_id"], row["blind_label"], row["call_id"])
            for row in event_manifest["events"]
        }
        response_calls = {
            (row["layer"], row["skill_id"], row["case_id"], row["blind_label"], row["call_id"])
            for row in response_manifest["responses"]
        }
        if not response_calls.issubset(event_calls):
            raise ProtocolError("execution event manifest is missing one or more response calls")

        assignments = {
            (row.get("layer"), row.get("skill_id"), row.get("case_id"), row.get("blind_label")):
                row.get("arm")
            for row in reveal.get("assignments", [])
        }
        result_arms = {
            (record.get("layer"), record.get("skill_id"), record.get("case_id"), record.get("blind_label")):
                record.get("arm")
            for record in records
        }
        if assignments != result_arms:
            raise ProtocolError("allocation reveal does not exactly bind layer result arms")
    if administration_attestation.get("independent_of_skill_authors") is not True:
        raise ProtocolError("administrator independence is not attested")
    if administration_attestation.get("first_attempt_confirmed") is not True:
        raise ProtocolError("first attempt is not attested")
    if administration_attestation.get("tool_policy_parity_verified") is not True:
        raise ProtocolError("tool-policy parity is not attested")
    portfolio_results_sha256 = canonical_sha256(portfolio_results)
    if portfolio_results_file is None:
        results_bytes = canonical_bytes(portfolio_results)
        results_serialization = "canonical-json-bytes"
    else:
        if not portfolio_results_file.is_file():
            raise ProtocolError("portfolio results file is missing during verification assembly")
        results_bytes = portfolio_results_file.read_bytes()
        try:
            parsed_file = json.loads(results_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("portfolio results file must be UTF-8 JSON") from exc
        if parsed_file != portfolio_results:
            raise ProtocolError("portfolio results object differs from the exact source file")
        results_serialization = "source-file-bytes"
    portfolio_results_file_sha256 = hashlib.sha256(results_bytes).hexdigest()
    evidence_hash = _evidence_component_hash(
        run_manifest=run_manifest, response_manifests=response_manifests,
        artifact_manifests=artifact_manifests, ratings_locks=ratings_locks,
        execution_event_manifests=execution_event_manifests,
        allocation_reveals=allocation_reveals,
    )
    if administration_attestation.get("evidence_bundle_sha256") != evidence_hash:
        raise ProtocolError("administration attestation does not bind the complete evidence component set")
    return {
        "$schema": "portfolio-verification-v3.schema.json", "schema_version": "3.0",
        "verification_id": verification_id, "verified_at": verified_at, "verifier_id": verifier_id,
        "verification_key_id": verification_key_id, "run_manifest": run_manifest,
        "holdout_lock": holdout_lock, "first_attempt_registry": first_attempt_registry,
        "allocation_reveals": allocation_reveals,
        "response_manifests": response_manifests,
        "artifact_manifests": artifact_manifests,
        "ratings_locks": ratings_locks,
        "execution_event_manifests": execution_event_manifests,
        "portfolio_results_sha256": portfolio_results_sha256,
        "portfolio_results_file_sha256": portfolio_results_file_sha256,
        "portfolio_results_size_bytes": len(results_bytes),
        "portfolio_results_serialization": results_serialization,
        "evidence_components_sha256": evidence_hash,
        "administration_attestation": administration_attestation,
    }


def verify_signature(bundle: dict[str, Any], public_key_hex: str) -> bool:
    if Ed25519PublicKey is None:
        raise ProtocolError("cryptography is required for Ed25519 operations")
    signature = bundle.get("verification_signature_ed25519")
    payload = {key: value for key, value in bundle.items() if key != "verification_signature_ed25519"}
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(bytes.fromhex(signature), canonical_bytes(payload))
        return True
    except Exception:
        return False


def _secret(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 32:
        raise ProtocolError("secret file must contain at least 32 characters")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit-tasks")
    audit.add_argument("tasks", type=Path)
    audit.add_argument("--known", type=Path)

    freeze = sub.add_parser("freeze-holdout")
    freeze.add_argument("tasks", type=Path); freeze.add_argument("--known", type=Path)
    freeze.add_argument("--freeze-id", required=True); freeze.add_argument("--frozen-at", required=True)
    freeze.add_argument("--secret-file", type=Path, required=True); freeze.add_argument("--nonce", required=True)
    freeze.add_argument("--output", type=Path, required=True)

    allocate = sub.add_parser("allocate")
    allocate.add_argument("tasks", type=Path); allocate.add_argument("--freeze-id", required=True)
    allocate.add_argument("--secret-file", type=Path, required=True); allocate.add_argument("--nonce", required=True)
    allocate.add_argument("--output", type=Path, required=True)

    first = sub.add_parser("register-first-attempt")
    first.add_argument("lock", type=Path); first.add_argument("--run-id", required=True)
    first.add_argument("--registered-at", required=True); first.add_argument("--execution-started-at", required=True)
    first.add_argument("--output", type=Path, required=True)

    precommit = sub.add_parser("rater-precommit")
    precommit.add_argument("--primary-raters", nargs=2, required=True)
    precommit.add_argument("--adjudicator", required=True); precommit.add_argument("--rubric", type=Path, required=True)
    precommit.add_argument("--output", type=Path, required=True)

    baseline = sub.add_parser("baseline-selection")
    baseline.add_argument("choices", type=Path); baseline.add_argument("--selected-at", required=True)
    baseline.add_argument("--output", type=Path, required=True)

    manifest = sub.add_parser("run-manifest")
    manifest.add_argument("--run-id", required=True); manifest.add_argument("--skill-commit", required=True)
    manifest.add_argument("--baseline-selection", type=Path, required=True); manifest.add_argument("--rater-precommit", type=Path, required=True)
    manifest.add_argument("--holdout-lock", type=Path, required=True); manifest.add_argument("--first-attempt", type=Path, required=True)
    manifest.add_argument("--execution-started-at", required=True); manifest.add_argument("--output", type=Path, required=True)

    response = sub.add_parser("responses")
    response.add_argument("rows", type=Path); response.add_argument("--output", type=Path, required=True)

    artifacts = sub.add_parser("artifacts")
    artifacts.add_argument("rows", type=Path); artifacts.add_argument("--output", type=Path, required=True)

    events = sub.add_parser("execution-events")
    events.add_argument("rows", type=Path); events.add_argument("--output", type=Path, required=True)

    packet = sub.add_parser("rater-packet")
    packet.add_argument("tasks", type=Path); packet.add_argument("responses", type=Path)
    packet.add_argument("--rater-id", required=True); packet.add_argument("--rubric", type=Path, required=True)
    packet.add_argument("--output", type=Path, required=True)

    reveal = sub.add_parser("reveal")
    reveal.add_argument("tasks", type=Path); reveal.add_argument("--freeze-id", required=True)
    reveal.add_argument("--secret-file", type=Path, required=True); reveal.add_argument("--nonce", required=True)
    reveal.add_argument("--ratings-lock", type=Path, required=True)
    reveal.add_argument("--ratings-completed-at", required=True); reveal.add_argument("--revealed-at", required=True)
    reveal.add_argument("--output", type=Path, required=True)

    ratings_lock = sub.add_parser("ratings-lock")
    ratings_lock.add_argument("results", type=Path); ratings_lock.add_argument("--layer", choices=TASK_LAYERS, required=True)
    ratings_lock.add_argument("--sealed-at", required=True); ratings_lock.add_argument("--response-manifest", type=Path, required=True)
    ratings_lock.add_argument("--output", type=Path, required=True)

    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("ratings", type=Path); aggregate.add_argument("reveal", type=Path)
    aggregate.add_argument("--iterations", type=int, default=10000); aggregate.add_argument("--output", type=Path, required=True)

    keygen = sub.add_parser("keygen")
    keygen.add_argument("--private-key", type=Path, required=True); keygen.add_argument("--key-id", required=True)
    keygen.add_argument("--registration-output", type=Path, required=True)

    sign = sub.add_parser("sign")
    sign.add_argument("bundle", type=Path); sign.add_argument("--private-key", type=Path, required=True)
    sign.add_argument("--output", type=Path, required=True)

    component_hash = sub.add_parser("evidence-components")
    component_hash.add_argument("--run-manifest", type=Path, required=True)
    component_hash.add_argument("--allocation-reveals", nargs="+", type=Path, required=True)
    component_hash.add_argument("--response-manifests", nargs="+", type=Path, required=True)
    component_hash.add_argument("--artifact-manifests", nargs="+", type=Path, required=True)
    component_hash.add_argument("--ratings-locks", nargs="+", type=Path, required=True)
    component_hash.add_argument("--execution-event-manifests", nargs="+", type=Path, required=True)
    component_hash.add_argument("--output", type=Path, required=True)

    assemble = sub.add_parser("assemble-verification")
    assemble.add_argument("--verification-id", required=True); assemble.add_argument("--verified-at", required=True)
    assemble.add_argument("--verifier-id", required=True); assemble.add_argument("--verification-key-id", required=True)
    assemble.add_argument("--run-manifest", type=Path, required=True); assemble.add_argument("--holdout-lock", type=Path, required=True)
    assemble.add_argument("--first-attempt", type=Path, required=True); assemble.add_argument("--allocation-reveals", nargs="+", type=Path, required=True)
    assemble.add_argument("--response-manifests", nargs="+", type=Path, required=True)
    assemble.add_argument("--artifact-manifests", nargs="+", type=Path, required=True)
    assemble.add_argument("--ratings-locks", nargs="+", type=Path, required=True)
    assemble.add_argument("--execution-event-manifests", nargs="+", type=Path, required=True)
    assemble.add_argument("--portfolio-results", type=Path, required=True)
    assemble.add_argument("--attestation", type=Path, required=True); assemble.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "audit-tasks":
            known = load_json(args.known).get("fingerprints", []) if args.known else []
            result = validate_task_document(load_json(args.tasks), known_fingerprints=known)
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        if args.command == "freeze-holdout":
            document = load_json(args.tasks)
            known = load_json(args.known).get("fingerprints", []) if args.known else []
            commitment = allocation_commitment(
                _secret(args.secret_file), args.nonce, args.freeze_id, document["layer"]
            )
            result = build_holdout_lock(document, freeze_id=args.freeze_id, frozen_at=args.frozen_at, commitment_sha256=commitment, known_fingerprints=known)
            write_json(args.output, result); return 0
        if args.command == "allocate":
            result = {"assignments": latin_square_assignments(load_json(args.tasks), secret=_secret(args.secret_file), nonce=args.nonce, freeze_id=args.freeze_id)}
            write_json(args.output, result); return 0
        if args.command == "register-first-attempt":
            result = build_first_attempt_registry(load_json(args.lock), run_id=args.run_id, registered_at=args.registered_at, execution_started_at=args.execution_started_at)
            write_json(args.output, result); return 0
        if args.command == "rater-precommit":
            result = build_rater_precommit(args.primary_raters, adjudicator_id=args.adjudicator, rubric_sha256=sha256_file(args.rubric))
            write_json(args.output, result); return 0
        if args.command == "baseline-selection":
            source = load_json(args.choices)
            result = build_baseline_selection(source["choices"], selected_at=args.selected_at)
            write_json(args.output, result); return 0
        if args.command == "run-manifest":
            result = build_run_manifest(
                run_id=args.run_id, skill_commit=args.skill_commit,
                baseline_selection=load_json(args.baseline_selection), rater_precommit=load_json(args.rater_precommit),
                holdout_lock=load_json(args.holdout_lock), first_attempt_registry=load_json(args.first_attempt),
                execution_started_at=args.execution_started_at,
            )
            write_json(args.output, result); return 0
        if args.command == "responses":
            source = load_json(args.rows)
            result = build_response_manifest(
                source["responses"], run_id=source["run_id"], layer=source["layer"]
            ); write_json(args.output, result); return 0
        if args.command == "artifacts":
            source = load_json(args.rows)
            result = build_artifact_manifest(
                source["artifacts"], run_id=source["run_id"], layer=source["layer"]
            ); write_json(args.output, result); return 0
        if args.command == "execution-events":
            source = load_json(args.rows)
            result = build_execution_event_manifest(
                source["events"], run_id=source["run_id"], layer=source["layer"]
            ); write_json(args.output, result); return 0
        if args.command == "rater-packet":
            result = build_rater_packet(load_json(args.tasks), load_json(args.responses), rater_id=args.rater_id, rubric_sha256=sha256_file(args.rubric))
            write_json(args.output, result); return 0
        if args.command == "reveal":
            result = build_allocation_reveal(
                load_json(args.tasks), secret=_secret(args.secret_file), nonce=args.nonce,
                freeze_id=args.freeze_id, ratings_completed_at=args.ratings_completed_at,
                revealed_at=args.revealed_at, ratings_lock=load_json(args.ratings_lock),
            )
            write_json(args.output, result); return 0
        if args.command == "ratings-lock":
            source = load_json(args.results)
            manifest_value = load_json(args.response_manifest)
            result = build_ratings_lock(
                source["records"], run_id=source["run_id"], layer=args.layer,
                sealed_at=args.sealed_at,
                response_manifest_sha256=manifest_value["response_manifest_sha256"],
            )
            write_json(args.output, result); return 0
        if args.command == "aggregate":
            result = aggregate_three_arm(load_json(args.ratings)["ratings"], load_json(args.reveal), iterations=args.iterations)
            write_json(args.output, result); return 0
        if args.command == "keygen":
            result = generate_ed25519_key(args.private_key, key_id=args.key_id)
            write_json(args.registration_output, result); return 0
        if args.command == "sign":
            result = sign_verification_bundle(load_json(args.bundle), args.private_key)
            write_json(args.output, result); return 0
        if args.command == "evidence-components":
            digest = _evidence_component_hash(
                run_manifest=load_json(args.run_manifest),
                response_manifests=[load_json(path) for path in args.response_manifests],
                artifact_manifests=[load_json(path) for path in args.artifact_manifests],
                ratings_locks=[load_json(path) for path in args.ratings_locks],
                execution_event_manifests=[load_json(path) for path in args.execution_event_manifests],
                allocation_reveals=[load_json(path) for path in args.allocation_reveals],
            )
            write_json(args.output, {"evidence_components_sha256": digest}); return 0
        if args.command == "assemble-verification":
            result = assemble_verification_bundle(
                verification_id=args.verification_id, verified_at=args.verified_at,
                verifier_id=args.verifier_id, verification_key_id=args.verification_key_id,
                run_manifest=load_json(args.run_manifest), holdout_lock=load_json(args.holdout_lock),
                first_attempt_registry=load_json(args.first_attempt),
                allocation_reveals=[load_json(path) for path in args.allocation_reveals],
                response_manifests=[load_json(path) for path in args.response_manifests],
                artifact_manifests=[load_json(path) for path in args.artifact_manifests],
                ratings_locks=[load_json(path) for path in args.ratings_locks],
                execution_event_manifests=[load_json(path) for path in args.execution_event_manifests],
                portfolio_results=load_json(args.portfolio_results),
                portfolio_results_file=args.portfolio_results,
                administration_attestation=load_json(args.attestation),
            )
            write_json(args.output, result); return 0
    except ProtocolError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
