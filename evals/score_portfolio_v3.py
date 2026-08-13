#!/usr/bin/env python3
"""Score the twenty-skill evaluation protocol from raw case-level records.

The scorer intentionally withholds official composite scores when any evidence
layer is absent.  Structural checks are reported as L1 engineering evidence,
never promoted to task capability by default values or imputation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised by the CLI environment gate
    jsonschema = None

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - exercised by the CLI environment gate
    InvalidSignature = None
    Ed25519PublicKey = None


LAYER_IDS = (
    "L1_contract_conformance",
    "L2_deterministic_function",
    "L3_controlled_task_capability",
    "L4_frozen_holdout_generalization",
)
TASK_ARMS = ("no_skill", "strongest_open_source_baseline", "distilled_skill")
BASE_DIR = Path(__file__).resolve().parent
TRUSTED_DOCUMENTS = {
    "protocol": "portfolio-protocol-v3.json",
    "matrix": "skill-evaluation-matrix-v3.json",
}
TRUSTED_SCHEMAS = {
    "protocol": "portfolio-protocol-v3.schema.json",
    "matrix": "skill-evaluation-matrix-v3.schema.json",
    "results": "portfolio-results-v3.schema.json",
    "verification": "portfolio-verification-v3.schema.json",
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of an empty sample")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _interval(values: list[float], confidence_level: float) -> list[float]:
    alpha = (1.0 - confidence_level) / 2.0
    return [round(_percentile(values, alpha), 3), round(_percentile(values, 1.0 - alpha), 3)]


def _wilson_interval(successes: int, total: int, confidence_level: float) -> list[float] | None:
    if total <= 0:
        return None
    # Protocol v3 fixes the confidence level at 95%; keep the implementation
    # dependency-free and reject unsupported levels rather than approximating.
    if not math.isclose(confidence_level, 0.95, abs_tol=1e-12):
        raise ValueError("Wilson interval currently supports confidence_level=0.95 only")
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = (proportion + z * z / (2.0 * total)) / denominator
    half = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)) / denominator
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        raise ValueError("cannot calculate mean of empty sample")
    return sum(materialized) / len(materialized)


def _stable_seed(base_seed: int, skill_id: str) -> int:
    digest = hashlib.sha256(skill_id.encode("utf-8")).digest()
    return base_seed ^ int.from_bytes(digest[:8], "big")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rating_score(rating: dict[str, Any], weights: dict[str, float]) -> float:
    dimensions = rating.get("dimension_scores")
    if not isinstance(dimensions, dict):
        raise ValueError("rating dimension_scores must be an object")
    return 25.0 * sum(float(weights[key]) * float(dimensions[key]) for key in weights)


def _gold_signature(rating: dict[str, Any]) -> tuple[tuple[str, bool], ...]:
    return tuple(sorted((str(row["check_id"]), bool(row["met"])) for row in rating.get("gold_checks", [])))


def _parse_datetime(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        _error(errors, f"{label} must be an ISO-8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _error(errors, f"{label} must be an ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        _error(errors, f"{label} must include a time zone")
        return None
    return parsed.astimezone(timezone.utc)


def _error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def _validate_schema(instance: dict[str, Any], schema: dict[str, Any], label: str, errors: list[str]) -> None:
    if jsonschema is None:
        _error(errors, "jsonschema is required to validate protocol-v3 evidence")
        return
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(instance)
    except jsonschema.exceptions.SchemaError as exc:
        _error(errors, f"{label} schema is invalid: {exc.message}")
    except jsonschema.exceptions.ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path) or "$"
        _error(errors, f"{label} schema validation failed at {path}: {exc.message}")


def _validate_trusted_inputs(
    protocol: dict[str, Any], matrix: dict[str, Any], results: dict[str, Any],
    verification: dict[str, Any] | None, supplied_schemas: dict[str, dict[str, Any] | None],
    errors: list[str],
) -> None:
    """Pin v3 scoring to repository-controlled documents and schemas.

    Accepting a caller-provided weaker protocol or schema would make every
    numerical gate attacker-controlled.  Schema arguments remain supported for
    API compatibility, but must be byte-semantically identical to the pinned
    repository versions.
    """
    trusted_protocol = _load_json(BASE_DIR / TRUSTED_DOCUMENTS["protocol"])
    trusted_matrix = _load_json(BASE_DIR / TRUSTED_DOCUMENTS["matrix"])
    if _canonical_sha256(protocol) != _canonical_sha256(trusted_protocol):
        _error(errors, "protocol document is not the repository-pinned protocol-v3")
    if _canonical_sha256(matrix) != _canonical_sha256(trusted_matrix):
        _error(errors, "capability matrix is not the repository-pinned matrix-v3")
    instances = {"protocol": protocol, "matrix": matrix, "results": results, "verification": verification}
    for label, filename in TRUSTED_SCHEMAS.items():
        trusted_schema = _load_json(BASE_DIR / filename)
        supplied = supplied_schemas.get(label)
        if supplied is not None and _canonical_sha256(supplied) != _canonical_sha256(trusted_schema):
            _error(errors, f"{label} schema is not the repository-pinned schema-v3")
        instance = instances[label]
        if instance is not None:
            _validate_schema(instance, trusted_schema, label, errors)


def _trusted_verifier_public_key(key_id: Any, errors: list[str]) -> bytes | None:
    registry = _load_json(BASE_DIR / "trusted-verifier-keys-v3.json")
    if registry.get("schema_version") != "3.0" or not isinstance(registry.get("keys"), list):
        _error(errors, "trusted verifier registry is malformed")
        return None
    matches = [row for row in registry["keys"] if isinstance(row, dict) and row.get("key_id") == key_id]
    if len(matches) != 1:
        _error(errors, "verification key was not independently pre-registered")
        return None
    row = matches[0]
    encoded_key = row.get("public_key_ed25519_hex")
    if row.get("active") is not True or not isinstance(encoded_key, str) or len(encoded_key) != 64:
        _error(errors, "pre-registered verification key is inactive or malformed")
        return None
    try:
        return bytes.fromhex(encoded_key)
    except ValueError:
        _error(errors, "pre-registered verification public key is not hexadecimal")
        return None


def _validate_top_level(
    protocol: dict[str, Any], matrix: dict[str, Any], results: dict[str, Any]
) -> tuple[list[str], list[str], dict[str, set[str]]]:
    errors: list[str] = []
    warnings: list[str] = []
    if protocol.get("schema_version") != "3.0":
        _error(errors, "protocol schema_version must be 3.0")
    if results.get("schema_version") != "3.0":
        _error(errors, "results schema_version must be 3.0")
    if results.get("protocol_id") != protocol.get("protocol_id"):
        _error(errors, "results protocol_id does not match the protocol")
    if matrix.get("schema_version") != "3.0":
        _error(errors, "matrix schema_version must be 3.0")

    matrix_rows = matrix.get("skills")
    if not isinstance(matrix_rows, list):
        _error(errors, "matrix.skills must be an array")
        matrix_rows = []
    skill_ids = [row.get("skill_id") for row in matrix_rows if isinstance(row, dict)]
    if len(skill_ids) != 20 or len(set(skill_ids)) != 20:
        _error(errors, "matrix must define exactly twenty unique skills")
    capability_map: dict[str, set[str]] = {}
    for row in matrix_rows:
        if not isinstance(row, dict) or not isinstance(row.get("skill_id"), str):
            continue
        capabilities = row.get("capabilities", [])
        ids = [item.get("id") for item in capabilities if isinstance(item, dict)]
        if len(ids) < 5 or len(ids) != len(set(ids)) or not all(isinstance(item, str) for item in ids):
            _error(errors, f"{row['skill_id']}: capability IDs must contain at least five unique strings")
        capability_map[row["skill_id"]] = set(ids)
        baselines = row.get("strongest_open_source_baseline_candidates")
        if not isinstance(baselines, list) or not baselines:
            _error(errors, f"{row['skill_id']}: at least one baseline candidate is required")

    records = results.get("records")
    if not isinstance(records, list):
        _error(errors, "results.records must be an array")
        records = []
    baselines = results.get("baseline_selection")
    if not isinstance(baselines, dict):
        _error(errors, "baseline_selection must be an object")
    holdout = results.get("holdout_declaration")
    if not isinstance(holdout, dict):
        _error(errors, "holdout_declaration must be an object")
    deviations = results.get("protocol_deviations")
    if not isinstance(deviations, list):
        _error(errors, "protocol_deviations must be an array")
    commit = results.get("skill_commit")
    if records:
        if not isinstance(commit, str) or len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
            _error(errors, "skill_commit must be a forty-character lowercase hexadecimal Git commit when records exist")
    elif commit is not None:
        warnings.append("skill_commit is ignored because this declaration contains no evidence records.")
    if not records:
        warnings.append("No protocol-v3 evidence records were supplied; official scores are withheld.")
    return errors, warnings, capability_map


def _validate_task_bundle(
    results: dict[str, Any], expected_skills: set[str], matrix: dict[str, Any],
    verification: dict[str, Any] | None, errors: list[str]
) -> dict[tuple[str, str, str], str]:
    """Validate precommitted run, baseline, holdout and allocation evidence.

    The returned map binds each (skill, case, arm) to its revealed blind label.
    An absent or self-reported verification bundle is never release evidence.
    """
    records = [record for record in results.get("records", []) if isinstance(record, dict)]
    task_records = [record for record in records if record.get("layer") in (LAYER_IDS[2], LAYER_IDS[3])]
    if not task_records:
        return {}
    if not isinstance(verification, dict):
        _error(errors, "task evaluation requires a verified protocol-artifact bundle")
        return {}

    public_key = _trusted_verifier_public_key(verification.get("verification_key_id"), errors)
    if Ed25519PublicKey is None or InvalidSignature is None:
        _error(errors, "cryptography is required to verify independent Ed25519 evidence signatures")
    elif public_key is not None:
        signed_payload = {key: value for key, value in verification.items() if key != "verification_signature_ed25519"}
        payload = json.dumps(signed_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        supplied_signature = verification.get("verification_signature_ed25519")
        try:
            signature = bytes.fromhex(supplied_signature) if isinstance(supplied_signature, str) else b""
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
        except (ValueError, InvalidSignature):
            _error(errors, "verification bundle Ed25519 signature is invalid")

    manifest = verification.get("run_manifest", {})
    lock = verification.get("holdout_lock", {})
    registry = verification.get("first_attempt_registry", {})
    reveal = verification.get("allocation_reveal", {})
    attestation = verification.get("administration_attestation", {})
    if not all(isinstance(item, dict) for item in (manifest, lock, registry, reveal, attestation)):
        _error(errors, "verification artifact sections must be objects")
        return {}

    if results.get("run_manifest_sha256") != _canonical_sha256(manifest):
        _error(errors, "run_manifest_sha256 does not bind the supplied run manifest")
    if manifest.get("run_id") != results.get("run_id") or manifest.get("skill_commit") != results.get("skill_commit"):
        _error(errors, "run manifest does not bind run_id and skill_commit")
    if manifest.get("protocol_sha256") != results.get("protocol_sha256") or manifest.get("capability_matrix_sha256") != results.get("capability_matrix_sha256"):
        _error(errors, "run manifest does not bind protocol and capability matrix")
    if manifest.get("baseline_selection_sha256") != _canonical_sha256(results.get("baseline_selection", {})):
        _error(errors, "run manifest does not bind baseline selection")
    if manifest.get("rater_precommit_sha256") != _canonical_sha256(results.get("rater_precommit")):
        _error(errors, "run manifest does not bind rater precommit")
    if results.get("rater_precommit", {}).get("precommit_sha256") != _canonical_sha256({
        key: value for key, value in results.get("rater_precommit", {}).items() if key != "precommit_sha256"
    }):
        _error(errors, "rater precommit self-hash is invalid")
    precommit = results.get("rater_precommit", {})
    if precommit.get("adjudicator_id") in set(precommit.get("primary_rater_ids", [])):
        _error(errors, "adjudicator must be distinct from both primary raters")

    candidate_map = {
        row["skill_id"]: set(row.get("strongest_open_source_baseline_candidates", []))
        for row in matrix.get("skills", []) if isinstance(row, dict)
    }
    execution_started = _parse_datetime(manifest.get("execution_started_at"), "execution_started_at", errors)
    for skill_id in sorted(expected_skills):
        selection = results.get("baseline_selection", {}).get(skill_id)
        if not isinstance(selection, dict):
            _error(errors, f"{skill_id}: missing preselected strongest baseline")
            continue
        if selection.get("baseline_id") not in candidate_map.get(skill_id, set()):
            _error(errors, f"{skill_id}: selected baseline is not a declared candidate")
        if selection.get("selected_before_first_run") is not True:
            _error(errors, f"{skill_id}: baseline was not selected before first run")
        if selection.get("selection_lock_sha256") != _canonical_sha256({
            key: value for key, value in selection.items() if key != "selection_lock_sha256"
        }):
            _error(errors, f"{skill_id}: baseline selection lock hash is invalid")
        selected_at = _parse_datetime(selection.get("selected_at"), f"{skill_id}.selected_at", errors)
        if execution_started and selected_at and selected_at >= execution_started:
            _error(errors, f"{skill_id}: baseline selection is not earlier than execution")

    holdout_records = [record for record in task_records if record.get("layer") == LAYER_IDS[3]]
    if holdout_records:
        declaration = results.get("holdout_declaration", {})
        bound_sections = {
            "lock_sha256": lock,
            "first_attempt_registry_sha256": registry,
            "allocation_reveal_sha256": reveal,
            "independent_administration_attestation_sha256": attestation,
        }
        for field, artifact in bound_sections.items():
            if declaration.get(field) != _canonical_sha256(artifact):
                _error(errors, f"holdout declaration {field} does not bind the supplied artifact")
        if manifest.get("holdout_lock_sha256") != declaration.get("lock_sha256"):
            _error(errors, "run manifest does not bind the holdout lock")
        if manifest.get("first_attempt_registry_sha256") != declaration.get("first_attempt_registry_sha256"):
            _error(errors, "run manifest does not bind the first-attempt registry")
        if declaration.get("case_document_sha256") != lock.get("case_document_sha256"):
            _error(errors, "holdout declaration does not bind the case document")
        fingerprint_map = lock.get("case_fingerprints", {})
        if declaration.get("case_fingerprints_sha256") != _canonical_sha256(fingerprint_map):
            _error(errors, "holdout declaration does not bind the frozen fingerprint map")
        if lock.get("known_fingerprint_overlap_count") != 0:
            _error(errors, "holdout lock reports known-case fingerprint overlap")
        if lock.get("allocation_commitment_sha256") != declaration.get("allocation_commitment_sha256"):
            _error(errors, "holdout lock allocation commitment is not bound")
        if registry.get("genesis_sha256") != lock.get("attempt_registry_genesis_sha256"):
            _error(errors, "first-attempt registry genesis does not match holdout lock")
        if registry.get("freeze_id") != lock.get("freeze_id") or reveal.get("freeze_id") != lock.get("freeze_id"):
            _error(errors, "freeze IDs disagree across holdout artifacts")
        if registry.get("run_id") != results.get("run_id") or registry.get("attempt_number") != 1:
            _error(errors, "first-attempt registry does not reserve this run as attempt one")
        if registry.get("case_fingerprints") != fingerprint_map:
            _error(errors, "first-attempt registry fingerprint set differs from the holdout lock")
        if reveal.get("allocation_commitment_sha256") != lock.get("allocation_commitment_sha256"):
            _error(errors, "allocation reveal does not open the frozen commitment")
        expected_commitment = hashlib.sha256(
            (str(reveal.get("secret")) + "\0" + str(reveal.get("nonce")) + "\0" + lock.get("freeze_id", "")).encode("utf-8")
        ).hexdigest()
        if reveal.get("allocation_commitment_sha256") != expected_commitment:
            _error(errors, "allocation secret and nonce do not open the commitment")
        rated_at = _parse_datetime(reveal.get("ratings_completed_at"), "ratings_completed_at", errors)
        revealed_at = _parse_datetime(reveal.get("revealed_at"), "revealed_at", errors)
        if rated_at and revealed_at and revealed_at <= rated_at:
            _error(errors, "allocation reveal must occur after all ratings")
        frozen_at = _parse_datetime(lock.get("frozen_at"), "frozen_at", errors)
        registered_at = _parse_datetime(registry.get("registered_at"), "registered_at", errors)
        if frozen_at and registered_at and registered_at < frozen_at:
            _error(errors, "first-attempt registry predates the holdout freeze")
        if execution_started and registered_at and registered_at >= execution_started:
            _error(errors, "first-attempt registry is not earlier than execution")
        if attestation.get("run_id") != results.get("run_id"):
            _error(errors, "administration attestation does not bind the run")

        for record_index, record in enumerate(task_records):
            rating_rows = list(record.get("primary_ratings", []))
            if isinstance(record.get("adjudication"), dict):
                rating_rows.append(record["adjudication"])
            for rating_index, rating in enumerate(rating_rows):
                if not isinstance(rating, dict):
                    continue
                timestamp = _parse_datetime(
                    rating.get("rated_at"), f"task record {record_index} rating {rating_index} rated_at", errors
                )
                if execution_started and timestamp and timestamp < execution_started:
                    _error(errors, "a task rating predates execution start")
                if rated_at and timestamp and timestamp > rated_at:
                    _error(errors, "a task rating is later than ratings_completed_at")

        actual_holdout = {
            f"{record['skill_id']}::{record['case_id']}": record.get("case_fingerprint")
            for record in holdout_records
        }
        if actual_holdout != fingerprint_map:
            _error(errors, "executed holdout records do not exactly match the frozen fingerprint map")

    assignment_map: dict[tuple[str, str, str], str] = {}
    for assignment in reveal.get("assignments", []):
        if not isinstance(assignment, dict):
            continue
        key = (assignment.get("skill_id"), assignment.get("case_id"), assignment.get("arm"))
        if key in assignment_map:
            _error(errors, f"duplicate allocation assignment {key}")
        assignment_map[key] = assignment.get("blind_label")
    task_keys = {
        (record.get("skill_id"), record.get("case_id"), record.get("arm")) for record in task_records
    }
    if task_keys and set(assignment_map) != task_keys:
        _error(errors, "allocation reveal does not cover exactly all executed blinded task arms")
    return assignment_map


def _index_records(
    records: list[Any], expected_skills: set[str], capability_map: dict[str, set[str]],
    protocol: dict[str, Any], results: dict[str, Any],
    verified_allocations: dict[tuple[str, str, str], str], errors: list[str]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    indexed: dict[str, dict[str, list[dict[str, Any]]]] = {
        skill_id: {layer_id: [] for layer_id in LAYER_IDS} for skill_id in expected_skills
    }
    seen_non_task: set[tuple[str, str, str]] = set()
    seen_task_arm: set[tuple[str, str, str, str]] = set()
    response_bindings: dict[str, tuple[str, str, str, str]] = {}
    evidence_hashes: set[str] = set()
    rating_call_ids: set[str] = set()
    case_fingerprint_bindings: dict[tuple[str, str], tuple[str, str]] = {}
    expected_commit = results.get("skill_commit")
    expected_protocol_hash = results.get("protocol_sha256")
    expected_matrix_hash = results.get("capability_matrix_sha256")
    precommit = results.get("rater_precommit") if isinstance(results.get("rater_precommit"), dict) else {}
    expected_raters = set(precommit.get("primary_rater_ids", []))
    expected_adjudicator = precommit.get("adjudicator_id")
    expected_rubric = precommit.get("rubric_sha256")
    dimension_weights = protocol.get("rating", {}).get("dimension_weights", {})
    for position, raw in enumerate(records):
        label = f"record[{position}]"
        if not isinstance(raw, dict):
            _error(errors, f"{label} must be an object")
            continue
        skill_id = raw.get("skill_id")
        layer = raw.get("layer")
        case_id = raw.get("case_id")
        if skill_id not in expected_skills:
            _error(errors, f"{label}: unknown skill_id {skill_id!r}")
            continue
        if layer not in LAYER_IDS:
            _error(errors, f"{label}: unknown layer {layer!r}")
            continue
        if not isinstance(case_id, str) or not case_id:
            _error(errors, f"{label}: case_id must be non-empty")
            continue
        capabilities = raw.get("capability_ids")
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            _error(errors, f"{label}: capability_ids must be an array of strings")
            continue
        unknown_capabilities = set(capabilities) - capability_map.get(skill_id, set())
        if unknown_capabilities:
            _error(errors, f"{label}: unknown capability IDs {sorted(unknown_capabilities)}")
        if not isinstance(raw.get("critical_failure"), bool):
            _error(errors, f"{label}: critical_failure must be boolean")
        if raw.get("run_id") != results.get("run_id"):
            _error(errors, f"{label}: run_id is not bound to the result bundle")
        if raw.get("skill_commit") != expected_commit:
            _error(errors, f"{label}: skill_commit is not bound to the result bundle")
        if raw.get("protocol_sha256") != expected_protocol_hash:
            _error(errors, f"{label}: protocol_sha256 is not bound to the result bundle")
        if raw.get("capability_matrix_sha256") != expected_matrix_hash:
            _error(errors, f"{label}: capability_matrix_sha256 is not bound to the result bundle")
        evidence_sha = raw.get("evidence_sha256")
        if evidence_sha in evidence_hashes:
            _error(errors, f"{label}: evidence_sha256 is reused across case records")
        if isinstance(evidence_sha, str):
            evidence_hashes.add(evidence_sha)
        fingerprint = raw.get("case_fingerprint")
        if isinstance(fingerprint, str):
            fingerprint_key = (skill_id, fingerprint)
            prior_case = case_fingerprint_bindings.get(fingerprint_key)
            current_case = (layer, case_id)
            if prior_case is not None and prior_case != current_case:
                _error(errors, f"{label}: case_fingerprint is reused under another case or layer")
            case_fingerprint_bindings[fingerprint_key] = current_case

        if layer in (LAYER_IDS[0], LAYER_IDS[1]):
            key = (skill_id, layer, case_id)
            if key in seen_non_task:
                _error(errors, f"duplicate non-task record {key}")
            seen_non_task.add(key)
            if raw.get("arm") not in (None,):
                _error(errors, f"{label}: L1/L2 arm must be null or omitted")
            if not isinstance(raw.get("passed"), bool):
                _error(errors, f"{label}: L1/L2 passed must be boolean")
        else:
            arm = raw.get("arm")
            if arm not in TASK_ARMS:
                _error(errors, f"{label}: L3/L4 arm must be one of {TASK_ARMS}")
            else:
                key = (skill_id, layer, case_id, arm)
                if key in seen_task_arm:
                    _error(errors, f"duplicate task-arm record {key}")
                seen_task_arm.add(key)
            score = raw.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
                _error(errors, f"{label}: score must be between 0 and 100")
            gold_met, gold_total = raw.get("gold_met"), raw.get("gold_total")
            if not isinstance(gold_met, int) or not isinstance(gold_total, int) or gold_total < 1 or not 0 <= gold_met <= gold_total:
                _error(errors, f"{label}: gold_met/gold_total are invalid")
            digest = raw.get("response_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                _error(errors, f"{label}: response_sha256 must be lowercase SHA-256")
            ratings = raw.get("primary_ratings")
            if not isinstance(ratings, list) or len(ratings) != 2:
                _error(errors, f"{label}: exactly two primary ratings are required")
            else:
                rater_ids = [rating.get("rater_id") for rating in ratings if isinstance(rating, dict)]
                if len(rater_ids) != 2 or len(set(rater_ids)) != 2 or not all(isinstance(item, str) and item for item in rater_ids):
                    _error(errors, f"{label}: two distinct primary rater IDs are required")
                rating_scores = [rating.get("score") for rating in ratings if isinstance(rating, dict)]
                rating_critical = [rating.get("critical_failure") for rating in ratings if isinstance(rating, dict)]
                if set(rater_ids) != expected_raters:
                    _error(errors, f"{label}: primary raters do not match the run precommit")
                if any(rating.get("response_sha256") != digest for rating in ratings if isinstance(rating, dict)):
                    _error(errors, f"{label}: primary rating response hashes do not match the arm response")
                if any(rating.get("rubric_sha256") != expected_rubric for rating in ratings if isinstance(rating, dict)):
                    _error(errors, f"{label}: primary rating rubric hashes do not match the precommit")
                call_ids = [rating.get("call_id") for rating in ratings if isinstance(rating, dict)]
                if len(call_ids) != 2 or len(set(call_ids)) != 2:
                    _error(errors, f"{label}: primary call IDs must be distinct")
                for call_id in call_ids:
                    if call_id in rating_call_ids:
                        _error(errors, f"{label}: rating call_id is reused across records")
                    if isinstance(call_id, str):
                        rating_call_ids.add(call_id)
                recomputed_rater_scores: list[float] = []
                for rating in ratings:
                    if not isinstance(rating, dict):
                        continue
                    try:
                        recomputed = _rating_score(rating, dimension_weights)
                        recomputed_rater_scores.append(recomputed)
                        expected_score = 0.0 if rating.get("critical_failure") else recomputed
                        if not math.isclose(float(rating.get("score", -1)), expected_score, abs_tol=1e-9):
                            _error(errors, f"{label}: rater score does not equal recomputed weighted dimensions")
                    except (KeyError, TypeError, ValueError):
                        _error(errors, f"{label}: invalid dimension score payload")
                gold_signatures = [_gold_signature(rating) for rating in ratings if isinstance(rating, dict)]
                expected_gold_ids = raw.get("gold_check_ids")
                if not isinstance(expected_gold_ids, list) or raw.get("gold_criteria_sha256") != _canonical_sha256(expected_gold_ids):
                    _error(errors, f"{label}: gold criteria are not hash-bound to the case record")
                elif any({check_id for check_id, _ in signature} != set(expected_gold_ids) for signature in gold_signatures):
                    _error(errors, f"{label}: rater gold-check IDs do not match the case gold criteria")
                if len(gold_signatures) == 2 and {row[0] for row in gold_signatures[0]} != {row[0] for row in gold_signatures[1]}:
                    _error(errors, f"{label}: primary raters do not score the same gold-check IDs")
                if len(rating_scores) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in rating_scores):
                    needs_adjudication = abs(rating_scores[0] - rating_scores[1]) > protocol["rating"]["adjudicate_if_score_gap_exceeds_points"]
                    if len(rating_critical) == 2 and rating_critical[0] != rating_critical[1]:
                        needs_adjudication = True
                    if len(gold_signatures) == 2 and gold_signatures[0] != gold_signatures[1]:
                        needs_adjudication = True
                    adjudication = raw.get("adjudication")
                    selected_rating: dict[str, Any] | None = None
                    if needs_adjudication:
                        if not isinstance(adjudication, dict):
                            _error(errors, f"{label}: rater disagreement requires adjudication evidence")
                        else:
                            selected_rating = adjudication
                            adjudication_call = adjudication.get("call_id")
                            if adjudication_call in rating_call_ids:
                                _error(errors, f"{label}: adjudication call_id is reused")
                            if isinstance(adjudication_call, str):
                                rating_call_ids.add(adjudication_call)
                            if adjudication.get("rater_id") != expected_adjudicator:
                                _error(errors, f"{label}: adjudicator does not match the run precommit")
                            if adjudication.get("response_sha256") != digest or adjudication.get("rubric_sha256") != expected_rubric:
                                _error(errors, f"{label}: adjudication is not bound to response and rubric")
                            try:
                                adjudicated_score = _rating_score(adjudication, dimension_weights)
                                expected_adjudicated = 0.0 if adjudication.get("critical_failure") else adjudicated_score
                                if not math.isclose(float(adjudication.get("score", -1)), expected_adjudicated, abs_tol=1e-9):
                                    _error(errors, f"{label}: adjudicated score does not equal recomputed weighted dimensions")
                            except (KeyError, TypeError, ValueError):
                                _error(errors, f"{label}: invalid adjudication dimension payload")
                    elif adjudication is not None:
                        _error(errors, f"{label}: unexpected adjudication without a protocol trigger")
                    else:
                        selected_rating = None
                    if selected_rating is None and not needs_adjudication and len(recomputed_rater_scores) == 2:
                        canonical_score = _mean(recomputed_rater_scores)
                        canonical_critical = any(bool(value) for value in rating_critical)
                        canonical_gold = gold_signatures[0]
                    elif isinstance(selected_rating, dict):
                        canonical_score = float(selected_rating.get("score", -1))
                        canonical_critical = bool(selected_rating.get("critical_failure"))
                        canonical_gold = _gold_signature(selected_rating)
                    else:
                        canonical_score, canonical_critical, canonical_gold = -1.0, True, tuple()
                    if not math.isclose(float(raw.get("score", -2)), 0.0 if canonical_critical else canonical_score, abs_tol=1e-9):
                        _error(errors, f"{label}: record score is not the canonical rater aggregate")
                    if raw.get("critical_failure") is not canonical_critical:
                        _error(errors, f"{label}: record critical_failure is not the canonical rater verdict")
                    canonical_met = sum(bool(met) for _, met in canonical_gold)
                    if raw.get("gold_met") != canonical_met or raw.get("gold_total") != len(canonical_gold):
                        _error(errors, f"{label}: record gold totals are not the canonical rater verdict")
            if arm == "strongest_open_source_baseline":
                selection = results.get("baseline_selection", {}).get(skill_id, {})
                if raw.get("baseline_id") != selection.get("baseline_id"):
                    _error(errors, f"{label}: baseline arm is not bound to the preselected baseline")
            elif raw.get("baseline_id") is not None:
                _error(errors, f"{label}: non-baseline arm must have baseline_id null")
            if verified_allocations.get((skill_id, case_id, str(arm))) != raw.get("blind_label"):
                _error(errors, f"{label}: blind label does not match the verified allocation reveal")
            response_binding = (skill_id, layer, case_id, str(arm))
            prior = response_bindings.get(str(digest))
            if prior is not None and prior != response_binding:
                _error(errors, f"{label}: response_sha256 is reused across arms or cases")
            elif isinstance(digest, str):
                response_bindings[digest] = response_binding
        indexed[skill_id][layer].append(raw)
    return indexed


def _capability_counts(records: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    by_case: dict[str, set[str]] = defaultdict(set)
    for record in records:
        by_case[record["case_id"]].update(record.get("capability_ids", []))
    for capability_ids in by_case.values():
        counts.update(capability_ids)
    return counts


def _task_cases(
    skill_id: str, layer: str, records: list[dict[str, Any]], errors: list[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    cases: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        arm = record.get("arm")
        if arm in TASK_ARMS:
            cases[record["case_id"]][arm] = record
    for case_id, arms in cases.items():
        missing = set(TASK_ARMS) - set(arms)
        extra = set(arms) - set(TASK_ARMS)
        if missing or extra:
            _error(errors, f"{skill_id}/{layer}/{case_id}: incomplete arm set; missing={sorted(missing)}, extra={sorted(extra)}")
            continue
        strata = {record.get("stratum") for record in arms.values()}
        capability_sets = {tuple(sorted(record.get("capability_ids", []))) for record in arms.values()}
        fingerprints = {record.get("case_fingerprint") for record in arms.values()}
        instructions = {record.get("instruction_packet_sha256") for record in arms.values()}
        fixtures = {record.get("fixture_sha256") for record in arms.values()}
        gold_criteria = {record.get("gold_criteria_sha256") for record in arms.values()}
        blind_labels = {record.get("blind_label") for record in arms.values()}
        if len(strata) != 1:
            _error(errors, f"{skill_id}/{layer}/{case_id}: arms disagree on stratum")
        if len(capability_sets) != 1:
            _error(errors, f"{skill_id}/{layer}/{case_id}: arms disagree on capability IDs")
        if len(fingerprints) != 1 or len(instructions) != 1 or len(fixtures) != 1 or len(gold_criteria) != 1:
            _error(errors, f"{skill_id}/{layer}/{case_id}: arms are not bound to the same case packet")
        if blind_labels != {"A", "B", "C"}:
            _error(errors, f"{skill_id}/{layer}/{case_id}: blind labels must be a permutation of A/B/C")
    complete_cases = [arms for arms in cases.values() if set(arms) == set(TASK_ARMS)]
    for arm in TASK_ARMS:
        counts = Counter(arms[arm].get("blind_label") for arms in complete_cases)
        values = [counts[label] for label in ("A", "B", "C")]
        if values and max(values) - min(values) > 1:
            _error(errors, f"{skill_id}/{layer}: blind-label allocation is not balanced for arm {arm}")
    return cases


def _score_skill(
    skill_id: str,
    records_by_layer: dict[str, list[dict[str, Any]]],
    capability_ids: set[str],
    protocol: dict[str, Any],
    results: dict[str, Any],
    errors: list[str],
) -> tuple[dict[str, Any], list[float] | None]:
    layers = protocol["layers"]
    gates = protocol["stable_gate"]
    confidence = protocol["bootstrap"]["confidence_level"]
    iterations = int(protocol["bootstrap"]["iterations"])
    rng = random.Random(_stable_seed(int(protocol["bootstrap"]["seed"]), skill_id))
    report: dict[str, Any] = {"skill_id": skill_id, "layers": {}, "official_score": None, "official_score_ci95": None}
    layer_samples: dict[str, list[float]] = {}
    gate_failures: list[str] = []

    l1_records = records_by_layer[LAYER_IDS[0]]
    required_l1 = set(layers[LAYER_IDS[0]]["required_check_ids"])
    actual_l1 = {record["case_id"] for record in l1_records}
    l1_score = 100.0 * sum(bool(record.get("passed")) for record in l1_records) / len(l1_records) if l1_records else None
    l1_complete = len(l1_records) >= layers[LAYER_IDS[0]]["minimum_n_per_skill"] and actual_l1 == required_l1
    report["layers"][LAYER_IDS[0]] = {
        "status": "complete" if l1_complete else ("not_run" if not l1_records else "incomplete"),
        "n": len(l1_records),
        "score": round(l1_score, 3) if l1_score is not None else None,
        "ci95": None,
        "missing_required_checks": sorted(required_l1 - actual_l1),
        "interpretation": layers[LAYER_IDS[0]]["interpretation"],
    }
    if not l1_complete:
        gate_failures.append("L1 incomplete")
    elif not math.isclose(l1_score or 0.0, gates["L1_minimum_score"]):
        gate_failures.append("L1 score below gate")

    l2_records = records_by_layer[LAYER_IDS[1]]
    l2_success = sum(bool(record.get("passed")) for record in l2_records)
    l2_rate = l2_success / len(l2_records) if l2_records else None
    l2_ci = _wilson_interval(l2_success, len(l2_records), confidence)
    l2_caps = _capability_counts(l2_records)
    l2_missing_caps = sorted(capability_id for capability_id in capability_ids if l2_caps[capability_id] < layers[LAYER_IDS[1]]["minimum_cases_per_capability"])
    l2_boundary = sum(record.get("stratum") in {"negative", "boundary", "negative_or_boundary"} for record in l2_records)
    l2_critical = sum(bool(record.get("critical_failure")) for record in l2_records)
    l2_complete = (
        len(l2_records) >= layers[LAYER_IDS[1]]["minimum_n_per_skill"]
        and l2_boundary >= layers[LAYER_IDS[1]]["minimum_negative_or_boundary_cases"]
        and not l2_missing_caps
    )
    report["layers"][LAYER_IDS[1]] = {
        "status": "complete" if l2_complete else ("not_run" if not l2_records else "incomplete"),
        "n": len(l2_records),
        "negative_or_boundary_n": l2_boundary,
        "pass_rate": round(l2_rate, 4) if l2_rate is not None else None,
        "score": round(100.0 * l2_rate, 3) if l2_rate is not None else None,
        "ci95": l2_ci,
        "critical_failures": l2_critical,
        "missing_capability_coverage": l2_missing_caps,
        "interpretation": layers[LAYER_IDS[1]]["interpretation"],
    }
    if l2_complete:
        layer_samples[LAYER_IDS[1]] = [
            100.0 * _mean(float(rng.choice(l2_records).get("passed", False)) for _ in l2_records)
            for _ in range(iterations)
        ]
        if (l2_rate or 0.0) < gates["L2_minimum_pass_rate"]:
            gate_failures.append("L2 pass rate below gate")
        if l2_ci is None or l2_ci[0] < gates["L2_minimum_wilson_lower_bound"]:
            gate_failures.append("L2 Wilson lower bound below gate")
        if l2_critical > gates["L2_maximum_critical_failures"]:
            gate_failures.append("L2 critical failure")
    else:
        gate_failures.append("L2 incomplete")

    for layer_id in (LAYER_IDS[2], LAYER_IDS[3]):
        layer_records = records_by_layer[layer_id]
        cases = _task_cases(skill_id, layer_id, layer_records, errors)
        complete_cases = {
            case_id: arms for case_id, arms in cases.items() if set(arms) == set(TASK_ARMS)
        }
        strata = Counter(next(iter(arms.values())).get("stratum") for arms in complete_cases.values())
        capability_counts = _capability_counts(layer_records)
        missing_caps = sorted(
            capability_id
            for capability_id in capability_ids
            if capability_counts[capability_id] < layers[layer_id]["minimum_cases_per_capability"]
        )
        missing_strata = {
            stratum: layers[layer_id]["minimum_per_stratum"] - strata[stratum]
            for stratum in layers[layer_id]["required_strata"]
            if strata[stratum] < layers[layer_id]["minimum_per_stratum"]
        }
        distilled = [float(arms["distilled_skill"]["score"]) for arms in complete_cases.values()]
        gains = [
            float(arms["distilled_skill"]["score"])
            - max(float(arms["no_skill"]["score"]), float(arms["strongest_open_source_baseline"]["score"]))
            for arms in complete_cases.values()
        ]
        gold_met = sum(int(arms["distilled_skill"]["gold_met"]) for arms in complete_cases.values())
        gold_total = sum(int(arms["distilled_skill"]["gold_total"]) for arms in complete_cases.values())
        critical = sum(bool(arms["distilled_skill"]["critical_failure"]) for arms in complete_cases.values())
        unresolved = sum(
            record.get("adjudication_resolved") is False
            for arms in complete_cases.values()
            for record in arms.values()
        )
        layer_complete = (
            len(complete_cases) >= layers[layer_id]["minimum_n_per_skill"]
            and not missing_strata
            and not missing_caps
            and unresolved == 0
        )
        if layer_id == LAYER_IDS[3]:
            holdout = results.get("holdout_declaration", {})
            holdout_ready = all(
                holdout.get(field) is True
                for field in ("frozen", "unseen", "first_attempt", "independent_administration")
            ) and isinstance(holdout.get("lock_sha256"), str)
            layer_complete = layer_complete and holdout_ready
        else:
            holdout_ready = None

        if complete_cases:
            score_bootstrap: list[float] = []
            gain_bootstrap: list[float] = []
            for _ in range(iterations):
                selected = [rng.randrange(len(distilled)) for _ in distilled]
                score_bootstrap.append(_mean(distilled[index] for index in selected))
                gain_bootstrap.append(_mean(gains[index] for index in selected))
            score_ci = _interval(score_bootstrap, confidence)
            gain_ci = _interval(gain_bootstrap, confidence)
        else:
            score_bootstrap, gain_bootstrap, score_ci, gain_ci = [], [], None, None
        report["layers"][layer_id] = {
            "status": "complete" if layer_complete else ("not_run" if not layer_records else "incomplete"),
            "n_cases": len(complete_cases),
            "n_arm_records": len(layer_records),
            "strata": dict(sorted(strata.items(), key=lambda item: str(item[0]))),
            "distilled_mean_score": round(_mean(distilled), 3) if distilled else None,
            "distilled_score_ci95": score_ci,
            "gold_check_rate": round(gold_met / gold_total, 4) if gold_total else None,
            "mean_gain_over_strongest_paired_baseline": round(_mean(gains), 3) if gains else None,
            "gain_ci95": gain_ci,
            "distilled_critical_failures": critical,
            "unresolved_adjudications": unresolved,
            "missing_strata": missing_strata,
            "missing_capability_coverage": missing_caps,
            "holdout_ready": holdout_ready,
            "interpretation": layers[layer_id]["interpretation"],
        }
        if layer_complete:
            layer_samples[layer_id] = score_bootstrap
            mean_score = _mean(distilled)
            gold_rate = gold_met / gold_total if gold_total else 0.0
            mean_gain = _mean(gains)
            prefix = "L3" if layer_id == LAYER_IDS[2] else "L4"
            if mean_score < gates[f"{prefix}_minimum_distilled_mean_score"]:
                gate_failures.append(f"{prefix} distilled mean below gate")
            if gold_rate < gates[f"{prefix}_minimum_gold_check_rate"]:
                gate_failures.append(f"{prefix} gold-check rate below gate")
            if mean_gain < gates[f"{prefix}_minimum_mean_gain_over_strongest_baseline"]:
                gate_failures.append(f"{prefix} mean gain below gate")
            if critical > gates[f"{prefix}_maximum_distilled_critical_failures"]:
                gate_failures.append(f"{prefix} critical failure")
            if layer_id == LAYER_IDS[2] and (gain_ci is None or gain_ci[0] < gates["L3_minimum_gain_ci_lower_bound"]):
                gate_failures.append("L3 gain interval below gate")
            if layer_id == LAYER_IDS[3] and gates["L4_require_positive_gain_ci"] and (gain_ci is None or gain_ci[0] <= 0):
                gate_failures.append("L4 gain interval is not strictly positive")
        else:
            gate_failures.append(f"{('L3' if layer_id == LAYER_IDS[2] else 'L4')} incomplete")

    all_layers_complete = all(report["layers"][layer_id]["status"] == "complete" for layer_id in LAYER_IDS)
    composite_samples: list[float] | None = None
    if all_layers_complete:
        weights = {layer_id: float(layers[layer_id]["weight"]) for layer_id in LAYER_IDS}
        l1_value = float(report["layers"][LAYER_IDS[0]]["score"])
        composite_samples = []
        for index in range(iterations):
            composite_samples.append(
                weights[LAYER_IDS[0]] * l1_value
                + weights[LAYER_IDS[1]] * layer_samples[LAYER_IDS[1]][index]
                + weights[LAYER_IDS[2]] * layer_samples[LAYER_IDS[2]][index]
                + weights[LAYER_IDS[3]] * layer_samples[LAYER_IDS[3]][index]
            )
        point = (
            weights[LAYER_IDS[0]] * l1_value
            + weights[LAYER_IDS[1]] * float(report["layers"][LAYER_IDS[1]]["score"])
            + weights[LAYER_IDS[2]] * float(report["layers"][LAYER_IDS[2]]["distilled_mean_score"])
            + weights[LAYER_IDS[3]] * float(report["layers"][LAYER_IDS[3]]["distilled_mean_score"])
        )
        report["official_score"] = round(point, 3)
        report["official_score_ci95"] = _interval(composite_samples, confidence)
        if point < gates["minimum_composite_score"]:
            gate_failures.append("composite score below gate")
        if report["official_score_ci95"][0] < gates["minimum_composite_ci_lower_bound"]:
            gate_failures.append("composite lower confidence bound below gate")

    if results.get("protocol_deviations"):
        gate_failures.append("protocol deviations present")
    baseline = results.get("baseline_selection", {}).get(skill_id)
    if all_layers_complete and not isinstance(baseline, dict):
        gate_failures.append("pinned strongest-baseline selection missing")
    report["gate_failures"] = sorted(set(gate_failures))
    if not any(records_by_layer.values()):
        report["status"] = "not_evaluated"
    elif not all_layers_complete:
        report["status"] = "incomplete_beta"
    elif report["gate_failures"]:
        report["status"] = "beta"
    else:
        report["status"] = "stable"
    return report, composite_samples


def score_portfolio(
    protocol: dict[str, Any], matrix: dict[str, Any], results: dict[str, Any],
    *, protocol_schema: dict[str, Any] | None = None,
    matrix_schema: dict[str, Any] | None = None,
    results_schema: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    verification_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    supplied_protocol = protocol
    supplied_matrix = matrix
    protocol = _load_json(BASE_DIR / TRUSTED_DOCUMENTS["protocol"])
    matrix = _load_json(BASE_DIR / TRUSTED_DOCUMENTS["matrix"])
    errors, warnings, capability_map = _validate_top_level(protocol, matrix, results)
    _validate_trusted_inputs(
        supplied_protocol, supplied_matrix, results, verification,
        {
            "protocol": protocol_schema,
            "matrix": matrix_schema,
            "results": results_schema,
            "verification": verification_schema,
        },
        errors,
    )
    raw_records = results.get("records") if isinstance(results.get("records"), list) else []
    records_exist = bool(raw_records)
    task_records_exist = any(
        isinstance(record, dict) and record.get("layer") in (LAYER_IDS[2], LAYER_IDS[3])
        for record in raw_records
    )
    expected_protocol_hash = _canonical_sha256(protocol)
    expected_matrix_hash = _canonical_sha256(matrix)
    if records_exist and results.get("protocol_sha256") != expected_protocol_hash:
        _error(errors, "protocol_sha256 does not bind the canonical protocol document")
    if records_exist and results.get("capability_matrix_sha256") != expected_matrix_hash:
        _error(errors, "capability_matrix_sha256 does not bind the canonical capability matrix")
    expected_skills = [row["skill_id"] for row in matrix.get("skills", []) if isinstance(row, dict) and isinstance(row.get("skill_id"), str)]
    if task_records_exist and set(results.get("baseline_selection", {})) != set(expected_skills):
        _error(errors, "task evaluation baseline_selection must cover exactly all twenty expected skills")
    if task_records_exist and not isinstance(results.get("rater_precommit"), dict):
        _error(errors, "task evaluation requires a run-level rater precommit")
    if task_records_exist and not isinstance(results.get("run_manifest_sha256"), str):
        _error(errors, "task evaluation requires a hash-bound run manifest")
    verified_allocations = _validate_task_bundle(
        results, set(expected_skills), matrix, verification, errors
    )
    indexed = _index_records(
        results.get("records", []) if isinstance(results.get("records"), list) else [],
        set(expected_skills), capability_map, protocol, results, verified_allocations, errors
    )
    development_fingerprints = {
        record.get("case_fingerprint") for record in results.get("records", [])
        if isinstance(record, dict) and record.get("layer") == LAYER_IDS[2]
    }
    holdout_fingerprints = {
        record.get("case_fingerprint") for record in results.get("records", [])
        if isinstance(record, dict) and record.get("layer") == LAYER_IDS[3]
    }
    overlap = development_fingerprints & holdout_fingerprints
    if overlap:
        _error(errors, "development and holdout case fingerprints overlap")
    skill_reports: list[dict[str, Any]] = []
    skill_bootstraps: dict[str, list[float]] = {}
    for skill_id in expected_skills:
        report, samples = _score_skill(skill_id, indexed[skill_id], capability_map[skill_id], protocol, results, errors)
        skill_reports.append(report)
        if samples is not None:
            skill_bootstraps[skill_id] = samples

    if errors:
        for report in skill_reports:
            if report["status"] == "stable":
                report["status"] = "invalid"
            if report["official_score"] is not None:
                report["diagnostic_unverified_score"] = report["official_score"]
                report["diagnostic_unverified_score_ci95"] = report["official_score_ci95"]
                report["official_score"] = None
                report["official_score_ci95"] = None
                report["gate_failures"] = sorted(set(report["gate_failures"] + ["invalid protocol evidence bundle"]))

    all_complete = len(skill_reports) == 20 and all(report["official_score"] is not None for report in skill_reports)
    all_stable = all_complete and all(report["status"] == "stable" for report in skill_reports)
    portfolio_score = None
    portfolio_ci = None
    if all_complete:
        portfolio_score = round(_mean(float(report["official_score"]) for report in skill_reports), 3)
        iterations = int(protocol["bootstrap"]["iterations"])
        portfolio_samples = [
            _mean(skill_bootstraps[skill_id][index] for skill_id in expected_skills)
            for index in range(iterations)
        ]
        portfolio_ci = _interval(portfolio_samples, float(protocol["bootstrap"]["confidence_level"]))

    layer_coverage = {
        layer_id: {
            "complete_skills": sum(report["layers"][layer_id]["status"] == "complete" for report in skill_reports),
            "expected_skills": 20,
        }
        for layer_id in LAYER_IDS
    }
    return {
        "schema_version": "3.0",
        "protocol_id": protocol.get("protocol_id"),
        "run_id": results.get("run_id"),
        "valid_input": not errors,
        "official_portfolio_score": portfolio_score if not errors else None,
        "official_portfolio_score_ci95": portfolio_ci if not errors else None,
        "portfolio_status": "invalid" if errors else ("stable" if all_stable else "beta"),
        "release_gate_passed": bool(not errors and all_stable),
        "score_withheld_reason": None if all_complete and not errors else "All twenty skills need complete L1-L4 evidence before an official portfolio score is reported.",
        "evidence_coverage": layer_coverage,
        "skills": skill_reports,
        "errors": sorted(errors),
        "warnings": warnings,
        "score_semantics": protocol["rating"]["score_semantics"],
        "claim_boundary": "L1 is engineering conformance; L3 is known development evidence; only complete frozen L4 evidence can support Stable promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--protocol", type=Path, default=Path(__file__).with_name("portfolio-protocol-v3.json"))
    parser.add_argument("--matrix", type=Path, default=Path(__file__).with_name("skill-evaluation-matrix-v3.json"))
    parser.add_argument("--protocol-schema", type=Path, default=Path(__file__).with_name("portfolio-protocol-v3.schema.json"))
    parser.add_argument("--matrix-schema", type=Path, default=Path(__file__).with_name("skill-evaluation-matrix-v3.schema.json"))
    parser.add_argument("--results-schema", type=Path, default=Path(__file__).with_name("portfolio-results-v3.schema.json"))
    parser.add_argument("--verification", type=Path)
    parser.add_argument("--verification-schema", type=Path, default=Path(__file__).with_name("portfolio-verification-v3.schema.json"))
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    report = score_portfolio(
        _load_json(arguments.protocol),
        _load_json(arguments.matrix),
        _load_json(arguments.results),
        protocol_schema=_load_json(arguments.protocol_schema),
        matrix_schema=_load_json(arguments.matrix_schema),
        results_schema=_load_json(arguments.results_schema),
        verification=_load_json(arguments.verification) if arguments.verification else None,
        verification_schema=_load_json(arguments.verification_schema) if arguments.verification else None,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "portfolio_status": report["portfolio_status"],
        "release_gate_passed": report["release_gate_passed"],
        "official_portfolio_score": report["official_portfolio_score"],
        "valid_input": report["valid_input"],
        "output": str(arguments.output),
    }, ensure_ascii=False))
    if not report["valid_input"]:
        return 1
    return 0 if report["release_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
