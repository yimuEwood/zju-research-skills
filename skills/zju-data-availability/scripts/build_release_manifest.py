#!/usr/bin/env python3
"""Build a local-byte manifest and a separate publication-readiness audit."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


IDENTIFIER_ROUTES = {"public_repository", "discipline_repository", "reused_public"}
LICENSE_ROUTES = {"public_repository", "discipline_repository", "reused_public", "within_article"}
VERIFICATION_STATUSES = {"verified", "pending", "failed", "not_checked"}


def _load_validator():
    path = Path(__file__).with_name("validate_data_inventory.py")
    spec = importlib.util.spec_from_file_location("validate_data_inventory", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load inventory validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _issue(artifact_id: str, reason: str, detail: str | None = None) -> dict[str, str]:
    issue = {"artifact_id": artifact_id, "reason": reason}
    if detail:
        issue["detail"] = detail
    return issue


def _verification_record(
    item: dict[str, Any], field: str, declared_value: str, required: bool
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    artifact_id = str(item["artifact_id"])
    raw = item.get(f"{field}_verification")
    issues: list[dict[str, str]] = []
    if not required:
        return {
            "required_for_route": False,
            "declared": bool(declared_value),
            "status": "not_required_for_route",
        }, issues
    if not declared_value:
        issues.append(_issue(artifact_id, f"{field}_not_declared"))
        return {"required_for_route": True, "declared": False, "status": "missing"}, issues
    if not isinstance(raw, dict):
        issues.append(_issue(artifact_id, f"{field}_not_verified"))
        return {
            "required_for_route": True,
            "declared": True,
            "status": "declared_unverified",
        }, issues
    status = str(raw.get("status") or "not_checked").strip().lower()
    if status not in VERIFICATION_STATUSES:
        issues.append(_issue(artifact_id, f"{field}_verification_status_invalid", status))
        return {
            "required_for_route": True,
            "declared": True,
            "status": "invalid_verification_record",
        }, issues
    record = {
        "required_for_route": True,
        "declared": True,
        "status": status,
        "checked_at": str(raw.get("checked_at") or "").strip() or None,
        "source": str(raw.get("source") or "").strip() or None,
    }
    verified_value = str(raw.get("value") or "").strip()
    if verified_value:
        record["verified_value"] = verified_value
    if status == "verified":
        if not record["checked_at"] or not record["source"]:
            record["status"] = "invalid_verification_record"
            issues.append(_issue(artifact_id, f"{field}_verification_evidence_missing"))
        elif verified_value and verified_value != declared_value:
            record["status"] = "verification_value_mismatch"
            issues.append(_issue(artifact_id, f"{field}_verification_value_mismatch"))
    else:
        issues.append(_issue(artifact_id, f"{field}_not_verified", status))
    return record, issues


def build(payload: dict[str, Any], inventory_path: Path) -> dict[str, Any]:
    report = _load_validator().validate(payload)
    if not report["valid"]:
        raise ValueError("inventory is invalid: " + json.dumps(report["findings"], ensure_ascii=False))
    base = inventory_path.resolve().parent
    entries: list[dict[str, Any]] = []
    local_unresolved: list[dict[str, str]] = []
    publication_unresolved: list[dict[str, str]] = []
    attention: list[dict[str, str]] = []

    for item in sorted(payload["artifacts"], key=lambda value: str(value["artifact_id"])):
        artifact_id = str(item["artifact_id"])
        route = str(item.get("access_route") or "").strip()
        location = str(item.get("current_location") or item.get("location") or "").strip()
        identifier = str(item.get("identifier") or "").strip()
        license_value = str(item.get("license") or "").strip()
        entry: dict[str, Any] = {
            "artifact_id": artifact_id,
            "artifact_class": item.get("artifact_class"),
            "version": item.get("version"),
            "format": item.get("format"),
            "access_route": route,
            "repository": item.get("repository"),
            "identifier": identifier or None,
            "license": license_value or None,
            "supports_claims": item.get("supports_claims", []),
            "supports_results": item.get("supports_results", []),
            "derived_from": item.get("derived_from", []),
            "current_location": location or "DETAILS_NOT_SUPPLIED",
        }

        if location and location != "DETAILS_NOT_SUPPLIED":
            candidate = Path(location)
            path = candidate if candidate.is_absolute() else base / candidate
            if path.is_file():
                computed = sha256_file(path)
                declared = str(item.get("sha256") or "").strip().lower()
                checksum_status = "verified_match" if declared == computed else "mismatch" if declared else "computed_no_prior_declaration"
                entry.update({
                    "file_present": True,
                    "bytes": path.stat().st_size,
                    "sha256": computed,
                    "checksum_verification": {
                        "status": checksum_status,
                        "declared": bool(declared),
                        "computed_from_local_bytes": True,
                        "declared_sha256": declared or None,
                    },
                })
                if declared and declared != computed:
                    local_unresolved.append(_issue(artifact_id, "declared_checksum_mismatch"))
                elif not declared:
                    attention.append(_issue(artifact_id, "checksum_not_predeclared"))
            else:
                entry.update({
                    "file_present": False,
                    "checksum_verification": {"status": "not_computed_file_missing", "declared": bool(item.get("sha256"))},
                })
                local_unresolved.append(_issue(artifact_id, "local_file_not_found"))
        else:
            entry.update({
                "file_present": False,
                "checksum_verification": {"status": "not_computed_location_missing", "declared": bool(item.get("sha256"))},
            })
            local_unresolved.append(_issue(artifact_id, "location_not_supplied"))

        identifier_record, identifier_issues = _verification_record(
            item, "identifier", identifier, route in IDENTIFIER_ROUTES
        )
        license_record, license_issues = _verification_record(
            item, "license", license_value, route in LICENSE_ROUTES
        )
        entry["identifier_verification"] = identifier_record
        entry["license_verification"] = license_record
        publication_unresolved.extend(identifier_issues)
        publication_unresolved.extend(license_issues)
        entries.append(entry)

    publication_unresolved = local_unresolved + publication_unresolved
    local_ready = not local_unresolved
    publication_ready = not publication_unresolved
    return {
        "schema_version": "2.0",
        "inventory": {"path": inventory_path.name, "sha256": sha256_file(inventory_path)},
        "artifacts": entries,
        "artifact_count": len(entries),
        "file_verified_count": sum(bool(item["file_present"]) for item in entries),
        "local_package_ready": local_ready,
        "publication_release_ready": publication_ready,
        "release_ready": publication_ready,
        "release_ready_deprecation": {
            "deprecated": True,
            "alias_of": "publication_release_ready",
            "migration": "Use local_package_ready for local-byte assembly and publication_release_ready for identifier/license-verified release claims.",
        },
        "local_unresolved": local_unresolved,
        "publication_unresolved": publication_unresolved,
        "unresolved": publication_unresolved,
        "verification_attention": attention,
        "note": "Local byte verification is separate from publication readiness. Identifier and license verification records are inventory assertions with recorded source/time, not live network checks by this offline script.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--require-publication-ready", action="store_true",
        help="return exit code 2 unless identifier/license verification also passes",
    )
    args = parser.parse_args()
    try:
        payload = json.loads(args.inventory.read_text(encoding="utf-8"))
        result = build(payload, args.inventory)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    ready = result["publication_release_ready"] if args.require_publication_ready else result["local_package_ready"]
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
