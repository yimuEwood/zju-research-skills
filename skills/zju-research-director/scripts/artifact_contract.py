#!/usr/bin/env python3
"""Validate the shared cross-skill artifact envelope."""

from __future__ import annotations

import hashlib
import copy
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import DEFAULT_REGISTRY_PATH, load_json_yaml, load_registry, stable_hash  # noqa: E402


DEFAULT_ARTIFACT_SCHEMA_PATH = SKILL_DIR / "references" / "artifact-envelope.schema.yaml"
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
TRUSTED_RUNNER_CHANNELS = {"trusted_ci_runner", "isolated_validation_runner"}


def _finding(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _timestamp(value: Any) -> bool:
    if not _nonempty(value):
        return False
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _producer_outputs(producer: str, registry: dict[str, dict[str, Any]]) -> set[str]:
    entry = registry.get(producer, {})
    declared = entry.get("produces", []) if isinstance(entry, dict) else []
    fallback = entry.get("fallback", {}) if isinstance(entry, dict) else {}
    fallback_outputs = fallback.get("produces", []) if isinstance(fallback, dict) else []
    return {_token(item) for item in [*declared, *fallback_outputs] if _nonempty(item)}


def _producer_validators(producer: str, registry: dict[str, dict[str, Any]]) -> set[str]:
    entry = registry.get(producer, {})
    validators = entry.get("validators", []) if isinstance(entry, dict) else []
    return {
        str(item.get("path"))
        for item in validators
        if isinstance(item, dict) and _nonempty(item.get("path"))
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validation_attestation_sha256(attestation: dict[str, Any]) -> str:
    """Hash a trusted-runner attestation without its self-referential hash field."""

    payload = copy.deepcopy(attestation)
    payload.pop("payload_sha256", None)
    return stable_hash(payload)


def _trusted_validation_attestation(
    artifact: dict[str, Any],
    validation: dict[str, Any],
    trusted_validation_receipts: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Verify an attestation supplied through the caller's trusted-runner channel.

    The separate input channel is the trust boundary.  A structurally identical
    object embedded in the mutable mission or artifact is intentionally ignored.
    This function does not claim that a local payload hash is a digital
    signature; the caller must authenticate the runner/file before supplying it.
    """

    if validation.get("method") != "deterministic":
        return None, ["only deterministic validation can receive trusted-runner attestation"]
    candidates = [
        item
        for item in (trusted_validation_receipts or [])
        if isinstance(item, dict)
        and item.get("artifact_id") == artifact.get("artifact_id")
        and item.get("validation_receipt_id") == validation.get("receipt_id")
    ]
    if not candidates:
        return None, ["no matching attestation was supplied through the trusted validation-runner channel"]

    issues: list[str] = []
    for attestation in candidates:
        current: list[str] = []
        required = {
            "schema_version": "1.0",
            "mission_id": (artifact.get("provenance") or {}).get("mission_id"),
            "artifact_id": artifact.get("artifact_id"),
            "artifact_type": artifact.get("artifact_type"),
            "producer": (artifact.get("provenance") or {}).get("producer"),
            "producer_step_id": (artifact.get("provenance") or {}).get("producer_step_id"),
            "content_sha256": artifact.get("content_sha256"),
            "validation_receipt_id": validation.get("receipt_id"),
            "validator": validation.get("validator"),
            "validator_version": validation.get("validator_version"),
            "command": validation.get("command"),
            "command_sha256": validation.get("command_sha256"),
            "exit_code": validation.get("exit_code"),
            "report_sha256": validation.get("report_sha256"),
            "checked_at": validation.get("checked_at"),
            "valid": True,
        }
        for field, expected in required.items():
            if field not in attestation:
                current.append(f"{field} is required")
                continue
            actual = attestation.get(field)
            if field == "exit_code":
                if type(actual) is not int or actual != 0 or actual != expected:
                    current.append("exit_code must be integer 0 and match the validation run")
            elif field == "valid":
                if actual is not True:
                    current.append("valid must be boolean true")
            elif actual != expected:
                current.append(f"{field} does not match the artifact validation run")
        for field in ("attestation_id", "runner_id", "runner_version", "trust_domain"):
            if not _nonempty(attestation.get(field)):
                current.append(f"{field} must be a non-empty string")
        if attestation.get("source_channel") not in TRUSTED_RUNNER_CHANNELS:
            current.append("source_channel is not an allowed trusted-runner channel")
        runner_id = str(attestation.get("runner_id") or "")
        producer = str((artifact.get("provenance") or {}).get("producer") or "")
        validator = str(validation.get("validator") or "")
        if runner_id and runner_id in {producer, validator}:
            current.append("runner_id must be independent of the artifact producer and validator")
        if not _timestamp(attestation.get("attested_at")):
            current.append("attested_at must be an ISO-8601 timestamp with timezone")
        payload_sha256 = attestation.get("payload_sha256")
        if not (isinstance(payload_sha256, str) and SHA256_PATTERN.fullmatch(payload_sha256)):
            current.append("payload_sha256 must contain 64 hexadecimal characters")
        elif payload_sha256.lower() != validation_attestation_sha256(attestation):
            current.append("payload_sha256 does not match the canonical attestation payload")
        if not current:
            return attestation, []
        issues.extend(f"{attestation.get('attestation_id', 'UNKNOWN')}: {item}" for item in current)
    return None, issues


def _resolve_inside_base(value: str, base_dir: Path) -> tuple[Path | None, str | None]:
    """Resolve a local path and reject absolute, relative, or symlink escapes."""

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(base_dir)
    except (OSError, ValueError):
        return None, f"Local path escapes validation base_dir {base_dir}"
    return candidate, None


def _format_error(artifact_type: Any, path: Path) -> str | None:
    token = _token(artifact_type)
    if token == "pptx":
        if path.suffix.lower() != ".pptx" or not zipfile.is_zipfile(path):
            return "PPTX artifact must be an OOXML ZIP package with a .pptx extension"
        try:
            with zipfile.ZipFile(path) as archive:
                members = set(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            return "PPTX artifact cannot be opened as an OOXML package"
        required = {"[Content_Types].xml", "ppt/presentation.xml"}
        if not required.issubset(members):
            return "PPTX artifact lacks required OOXML presentation members"
    if token in {"pdf", "fulltext_pdf", "paper_fulltext"}:
        try:
            with path.open("rb") as handle:
                signature = handle.read(5)
            if not signature.startswith(b"%PDF-"):
                return "PDF artifact does not have a PDF file signature"
        except OSError:
            return "PDF artifact cannot be read"
    return None


def validate_artifact(
    artifact: Any,
    mission_id: str | None = None,
    schema_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    base_dir: str | Path | None = None,
    trusted_validation_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return deterministic envelope findings for one artifact."""

    schema = load_json_yaml(schema_path or DEFAULT_ARTIFACT_SCHEMA_PATH)
    registry = load_registry(registry_path or DEFAULT_REGISTRY_PATH)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    content_verified = False
    local_validation_report_verified = False
    validation_receipt_verified = False
    trusted_validation_attestation_id: str | None = None
    root = Path(base_dir or Path.cwd()).expanduser().resolve()

    if not isinstance(artifact, dict):
        return {
            "valid": False,
            "artifact_id": None,
            "errors": [_finding("type", "$", "Artifact must be an object")],
            "warnings": [],
        }

    for field in schema.get("required_fields", []):
        if field not in artifact or artifact[field] is None or artifact[field] == "":
            errors.append(_finding("required", field, "Required artifact-envelope field is missing"))

    if artifact.get("schema_version") != schema.get("schema_version"):
        errors.append(_finding("schema_version", "schema_version", "Unsupported artifact-envelope version"))
    if not _nonempty(artifact.get("artifact_id")):
        errors.append(_finding("value", "artifact_id", "artifact_id must be a non-empty string"))
    if not _nonempty(artifact.get("artifact_type")):
        errors.append(_finding("value", "artifact_type", "artifact_type must be a non-empty string"))

    status = artifact.get("status")
    if status not in schema.get("statuses", []):
        errors.append(_finding("enum", "status", f"Unknown artifact status: {status}"))
    if not _timestamp(artifact.get("created_at")):
        errors.append(_finding("timestamp", "created_at", "created_at must be an ISO-8601 timestamp with timezone"))

    if status in {"created", "validated"}:
        locators = schema.get("locator_fields", [])
        if not any(_nonempty(artifact.get(field)) for field in locators):
            errors.append(_finding("locator", "$", "Created or validated artifact requires path, uri, or content_ref"))

    provenance = artifact.get("provenance")
    producer = ""
    if not isinstance(provenance, dict):
        errors.append(_finding("type", "provenance", "provenance must be an object"))
    else:
        for field in schema.get("provenance_required_fields", []):
            if not _nonempty(provenance.get(field)):
                errors.append(_finding("required", f"provenance.{field}", "Provenance field is required"))
        parent_mission_id = provenance.get("mission_id")
        if _nonempty(mission_id) and parent_mission_id != mission_id:
            errors.append(
                _finding(
                    "mission_mismatch",
                    "provenance.mission_id",
                    f"Artifact belongs to mission {parent_mission_id!r}, not {mission_id!r}",
                )
            )
        producer = str(provenance.get("producer") or "")
        allowed_external = set(schema.get("allowed_unregistered_producers", []))
        if _nonempty(producer) and producer not in registry and producer not in allowed_external:
            errors.append(_finding("unknown_producer", "provenance.producer", f"Unknown artifact producer: {producer}"))
        if producer in registry and _nonempty(artifact.get("artifact_type")):
            if not _nonempty(provenance.get("producer_step_id")):
                errors.append(
                    _finding(
                        "required",
                        "provenance.producer_step_id",
                        "Registered skill artifacts must identify the producing route step",
                    )
                )
            declared = _producer_outputs(producer, registry)
            artifact_type = _token(artifact["artifact_type"])
            if artifact_type not in declared:
                errors.append(
                    _finding(
                        "undeclared_output",
                        "artifact_type",
                        f"{producer} does not declare output type {artifact['artifact_type']!r}",
                    )
                )

    content_hash = artifact.get("content_sha256")
    if content_hash is not None and not (isinstance(content_hash, str) and SHA256_PATTERN.fullmatch(content_hash)):
        errors.append(_finding("sha256", "content_sha256", "content_sha256 must contain 64 hexadecimal characters"))

    artifact_path = artifact.get("path")
    artifact_candidate: Path | None = None
    if _nonempty(artifact_path):
        artifact_candidate, path_error = _resolve_inside_base(artifact_path, root)
        if path_error:
            errors.append(_finding("path_escape", "path", path_error))

    if status == "validated":
        if not _nonempty(artifact_path):
            errors.append(_finding("required_path", "path", "Validated artifact requires a local path for content verification"))
        elif artifact_candidate is not None:
            if not artifact_candidate.is_file():
                errors.append(_finding("missing_content", "path", f"Validated artifact file does not exist: {artifact_candidate}"))
            elif isinstance(content_hash, str) and SHA256_PATTERN.fullmatch(content_hash):
                actual_hash = _file_sha256(artifact_candidate)
                if actual_hash != content_hash.lower():
                    errors.append(
                        _finding(
                            "content_hash_mismatch",
                            "content_sha256",
                            f"Declared content SHA-256 does not match local file {artifact_candidate}",
                        )
                    )
                else:
                    content_verified = True
                format_error = _format_error(artifact.get("artifact_type"), artifact_candidate)
                if format_error:
                    errors.append(_finding("content_format", "path", format_error))
        if not (isinstance(content_hash, str) and SHA256_PATTERN.fullmatch(content_hash)):
            errors.append(_finding("required_hash", "content_sha256", "Validated artifact requires a content SHA-256"))
        validation = artifact.get("validation")
        if not isinstance(validation, dict):
            errors.append(_finding("type", "validation", "Validated artifact requires a validation object"))
        else:
            for field in schema.get("validation_required_fields", []):
                if field not in validation or validation[field] is None or validation[field] == "":
                    errors.append(_finding("required", f"validation.{field}", "Validation field is required"))
            if validation.get("status") != schema.get("validation_status"):
                errors.append(_finding("validation_status", "validation.status", "Validated artifact requires status passed"))
            if validation.get("method") not in schema.get("validation_methods", []):
                errors.append(_finding("enum", "validation.method", "Unknown artifact validation method"))
            if not _nonempty(validation.get("validator")):
                errors.append(_finding("value", "validation.validator", "validator must identify the check or reviewer"))
            if not _nonempty(validation.get("validator_version")):
                errors.append(_finding("value", "validation.validator_version", "validator_version must identify the validation implementation"))
            if not _nonempty(validation.get("receipt_id")):
                errors.append(_finding("value", "validation.receipt_id", "receipt_id must stably identify this validation event"))
            command = validation.get("command")
            if not isinstance(command, list) or not command or any(not _nonempty(item) for item in command):
                errors.append(_finding("type", "validation.command", "command must be a non-empty argument array"))
                canonical_command_sha256 = None
            else:
                canonical_command_sha256 = stable_hash(command)
            command_sha256 = validation.get("command_sha256")
            if not (isinstance(command_sha256, str) and SHA256_PATTERN.fullmatch(command_sha256)):
                errors.append(_finding("sha256", "validation.command_sha256", "command_sha256 must contain 64 hexadecimal characters"))
            elif canonical_command_sha256 is not None and command_sha256.lower() != canonical_command_sha256:
                errors.append(_finding("command_hash_mismatch", "validation.command_sha256", "command_sha256 does not match the canonical complete command array"))
            if type(validation.get("exit_code")) is not int or validation.get("exit_code") != 0:
                errors.append(_finding("exit_code", "validation.exit_code", "Validation command must have exit_code 0"))
            if not _timestamp(validation.get("checked_at")):
                errors.append(_finding("timestamp", "validation.checked_at", "checked_at must be an ISO-8601 timestamp with timezone"))
            validation_hash = validation.get("content_sha256")
            if validation_hash != content_hash:
                errors.append(
                    _finding(
                        "hash_mismatch",
                        "validation.content_sha256",
                        "Validation must bind to the current artifact content_sha256",
                    )
                )
            if validation.get("method") == "deterministic" and producer in registry:
                registered_validators = _producer_validators(producer, registry)
                if validation.get("validator") not in registered_validators:
                    errors.append(
                        _finding(
                            "unregistered_validator",
                            "validation.validator",
                            f"Deterministic validator is not registered for {producer}",
                        )
                    )
                elif isinstance(command, list):
                    validator_token = str(validation.get("validator")).replace("\\", "/").casefold()
                    command_tokens = [str(item).replace("\\", "/").casefold() for item in command]
                    if not any(item == validator_token or item.endswith("/" + validator_token) for item in command_tokens):
                        errors.append(
                            _finding(
                                "validator_command_mismatch",
                                "validation.command",
                                "Validation command does not invoke the registered validator",
                            )
                        )

            report_path = validation.get("report_path")
            report_hash = validation.get("report_sha256")
            if not _nonempty(report_path):
                errors.append(_finding("value", "validation.report_path", "report_path must identify the saved validation result"))
            else:
                report_candidate, report_path_error = _resolve_inside_base(report_path, root)
                if report_path_error:
                    errors.append(_finding("path_escape", "validation.report_path", report_path_error))
                elif report_candidate is not None and not report_candidate.is_file():
                    errors.append(
                        _finding(
                            "missing_validation_report",
                            "validation.report_path",
                            f"Validation report does not exist: {report_candidate}",
                        )
                    )
                elif report_candidate is not None and not (isinstance(report_hash, str) and SHA256_PATTERN.fullmatch(report_hash)):
                    errors.append(_finding("sha256", "validation.report_sha256", "report_sha256 must contain 64 hexadecimal characters"))
                elif report_candidate is not None:
                    actual_report_hash = _file_sha256(report_candidate)
                    if actual_report_hash != report_hash.lower():
                        errors.append(
                            _finding(
                                "validation_report_hash_mismatch",
                                "validation.report_sha256",
                                "Declared validation-report SHA-256 does not match the local report",
                            )
                        )
                    else:
                        try:
                            report = json.loads(report_candidate.read_text(encoding="utf-8-sig"))
                        except (OSError, json.JSONDecodeError):
                            errors.append(
                                _finding(
                                    "validation_report_format",
                                    "validation.report_path",
                                    "Validation report must be readable JSON",
                                )
                            )
                        else:
                            report_errors_before = len(errors)
                            if not isinstance(report, dict):
                                errors.append(_finding("validation_report_format", "validation.report_path", "Validation report root must be an object"))
                                report = {}
                            bindings = {
                                "receipt_id": validation.get("receipt_id"),
                                "artifact_id": artifact.get("artifact_id"),
                                "content_sha256": content_hash,
                                "validator": validation.get("validator"),
                                "validator_version": validation.get("validator_version"),
                                "command": command,
                                "command_sha256": canonical_command_sha256,
                                "exit_code": validation.get("exit_code"),
                                "checked_at": validation.get("checked_at"),
                                "valid": True,
                            }
                            for field, expected in bindings.items():
                                if field not in report:
                                    errors.append(_finding("validation_report_required", f"validation.report.{field}", "Validation report binding field is required"))
                                elif field == "valid" and report.get(field) is not True:
                                    errors.append(_finding("validation_report_binding_mismatch", f"validation.report.{field}", "Validation report valid must be boolean true"))
                                elif field == "exit_code" and (
                                    type(report.get(field)) is not int or report.get(field) != expected
                                ):
                                    errors.append(_finding("validation_report_binding_mismatch", f"validation.report.{field}", "Validation report exit_code must be integer 0 and match the validation receipt"))
                                elif field not in {"valid", "exit_code"} and report.get(field) != expected:
                                    errors.append(_finding("validation_report_binding_mismatch", f"validation.report.{field}", f"Validation report {field} does not match the validation receipt"))
                            if len(errors) == report_errors_before:
                                local_validation_report_verified = True

            if local_validation_report_verified:
                attestation, attestation_issues = _trusted_validation_attestation(
                    artifact,
                    validation,
                    trusted_validation_receipts,
                )
                if attestation is not None:
                    validation_receipt_verified = True
                    trusted_validation_attestation_id = attestation.get("attestation_id")
                else:
                    warnings.extend(
                        _finding("trusted_attestation", "validation", message)
                        for message in attestation_issues
                    )

    if status == "superseded" and not artifact.get("supersedes"):
        errors.append(_finding("supersedes", "supersedes", "Superseded artifact must identify the prior artifact"))

    if status == "created" and artifact.get("validation"):
        warnings.append(_finding("premature_validation", "validation", "Validation is recorded but artifact status is not validated"))

    return {
        "valid": not errors,
        "artifact_id": artifact.get("artifact_id"),
        "artifact_type": artifact.get("artifact_type"),
        "producer": producer or None,
        "content_verified": content_verified,
        "local_validation_report_verified": local_validation_report_verified,
        "validation_receipt_verified": validation_receipt_verified,
        "trusted_validation_attestation_id": trusted_validation_attestation_id,
        "errors": errors,
        "warnings": warnings,
    }
