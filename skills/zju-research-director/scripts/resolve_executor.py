#!/usr/bin/env python3
"""Resolve available execution providers without invoking them.

The resolver deliberately treats the host inventory as the only source of
availability truth.  It performs no imports, process launches, filesystem
probes, network calls, or credential checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable


PROVIDER_KINDS = {"skill", "tool", "script"}
WILDCARD = "*"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON from {path}: {exc}") from exc


def _string_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    return value


def validate_registry(registry: Any) -> dict[str, Any]:
    if not isinstance(registry, dict):
        raise ValueError("registry must be an object")
    capabilities = _string_list(registry.get("logical_capabilities"), "logical_capabilities", allow_empty=False)
    if len(set(capabilities)) != len(capabilities):
        raise ValueError("logical_capabilities contains duplicates")
    raw_providers = registry.get("providers")
    if not isinstance(raw_providers, list) or not raw_providers:
        raise ValueError("providers must be a non-empty list")

    provider_ids: set[str] = set()
    inventory_keys: set[str] = set()
    required = {
        "provider_id",
        "kind",
        "capabilities",
        "platforms",
        "accepts",
        "produces",
        "priority",
        "availability_probe",
        "invocation_hint",
        "output_adapter",
        "license_status",
    }
    for index, provider in enumerate(raw_providers):
        prefix = f"providers[{index}]"
        if not isinstance(provider, dict):
            raise ValueError(f"{prefix} must be an object")
        missing = sorted(required - set(provider))
        if missing:
            raise ValueError(f"{prefix} is missing: {', '.join(missing)}")
        provider_id = provider["provider_id"]
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError(f"{prefix}.provider_id must be a non-empty string")
        if provider_id in provider_ids:
            raise ValueError(f"duplicate provider_id: {provider_id}")
        provider_ids.add(provider_id)
        if provider["kind"] not in PROVIDER_KINDS:
            raise ValueError(f"{prefix}.kind must be one of {sorted(PROVIDER_KINDS)}")
        advertised = _string_list(provider["capabilities"], f"{prefix}.capabilities", allow_empty=False)
        unknown = sorted(set(advertised) - set(capabilities))
        if unknown:
            raise ValueError(f"{prefix}.capabilities contains unknown values: {', '.join(unknown)}")
        _string_list(provider["platforms"], f"{prefix}.platforms", allow_empty=False)
        _string_list(provider["accepts"], f"{prefix}.accepts")
        _string_list(provider["produces"], f"{prefix}.produces", allow_empty=False)
        if not isinstance(provider["priority"], int):
            raise ValueError(f"{prefix}.priority must be an integer")
        probe = provider["availability_probe"]
        if not isinstance(probe, dict) or probe.get("type") != "inventory_key":
            raise ValueError(f"{prefix}.availability_probe.type must be inventory_key")
        key = probe.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError(f"{prefix}.availability_probe.key must be a non-empty string")
        if key in inventory_keys:
            raise ValueError(f"duplicate availability inventory key: {key}")
        inventory_keys.add(key)
        for field in ("invocation_hint", "output_adapter", "license_status"):
            if not isinstance(provider[field], str) or not provider[field]:
                raise ValueError(f"{prefix}.{field} must be a non-empty string")
    return registry


def _inventory_keys(inventory: Any) -> set[str]:
    if not isinstance(inventory, dict):
        raise ValueError("inventory must be an object")
    available = inventory.get("available", [])
    if isinstance(available, list):
        if any(not isinstance(item, str) or not item for item in available):
            raise ValueError("inventory.available must contain only non-empty strings")
        return set(available)
    if isinstance(available, dict):
        invalid = [key for key, value in available.items() if not isinstance(key, str) or not isinstance(value, bool)]
        if invalid:
            raise ValueError("inventory.available object must map string keys to booleans")
        return {key for key, value in available.items() if value}
    raise ValueError("inventory.available must be a list or an object of booleans")


def normalize_requirements(requirements: Iterable[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(requirements, start=1):
        if isinstance(raw, str):
            requirement: dict[str, Any] = {"capability": raw}
        elif isinstance(raw, dict):
            requirement = dict(raw)
        else:
            raise ValueError(f"requirement {index} must be a capability string or object")
        capability = requirement.get("capability")
        if not isinstance(capability, str) or not capability:
            raise ValueError(f"requirement {index}.capability must be a non-empty string")
        requirement_id = requirement.get("requirement_id", f"REQ-{index:03d}")
        if not isinstance(requirement_id, str) or not requirement_id:
            raise ValueError(f"requirement {index}.requirement_id must be a non-empty string")
        accepts = requirement.get("accepts", [])
        produces = requirement.get("produces", [])
        _string_list(accepts, f"requirement {index}.accepts")
        _string_list(produces, f"requirement {index}.produces")
        normalized.append(
            {
                "requirement_id": requirement_id,
                "capability": capability,
                "accepts": sorted(set(accepts)),
                "produces": sorted(set(produces)),
            }
        )
    if not normalized:
        raise ValueError("at least one capability requirement is required")
    ids = [item["requirement_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("requirement_id values must be unique")
    return normalized


def _platform_compatible(provider: dict[str, Any], platform: str) -> bool:
    platforms = {item.casefold() for item in provider["platforms"]}
    return "any" in platforms or platform.casefold() in platforms


def _io_compatibility(provider: dict[str, Any], requirement: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    provider_accepts = set(provider["accepts"])
    provider_produces = set(provider["produces"])
    required_accepts = set(requirement["accepts"])
    required_produces = set(requirement["produces"])

    if required_accepts and WILDCARD not in provider_accepts and not (required_accepts & provider_accepts):
        reasons.append("no_compatible_input")
    if required_produces and WILDCARD not in provider_produces and not required_produces.issubset(provider_produces):
        reasons.append("missing_required_output")
    return not reasons, reasons


def _rank_key(provider: dict[str, Any], requirement: dict[str, Any]) -> tuple[Any, ...]:
    """Rank narrow compatible providers before generic providers, then priority."""
    accepts = set(provider["accepts"])
    produces = set(provider["produces"])
    required_accepts = set(requirement["accepts"])
    required_produces = set(requirement["produces"])
    wildcard_penalty = int(WILDCARD in accepts) + int(WILDCARD in produces)
    capability_extra = max(0, len(provider["capabilities"]) - 1)
    input_extra = len(accepts - required_accepts) if required_accepts and WILDCARD not in accepts else 0
    output_extra = len(produces - required_produces) if required_produces and WILDCARD not in produces else 0
    return (
        wildcard_penalty,
        capability_extra,
        input_extra + output_extra,
        -provider["priority"],
        provider["provider_id"],
    )


def resolve_executors(
    registry: Any,
    inventory: Any,
    requirements: Iterable[Any],
    *,
    platform: str | None = None,
) -> dict[str, Any]:
    registry = validate_registry(registry)
    normalized = normalize_requirements(requirements)
    available_keys = _inventory_keys(inventory)
    selected_platform = platform or inventory.get("platform")
    if not isinstance(selected_platform, str) or not selected_platform:
        raise ValueError("platform must be supplied by --platform or inventory.platform")

    selected: list[dict[str, Any]] = []
    candidate_groups: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    providers = sorted(registry["providers"], key=lambda item: item["provider_id"])
    known_capabilities = set(registry["logical_capabilities"])
    for requirement in normalized:
        assessed: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any]]] = []
        rows: list[dict[str, Any]] = []
        for provider in providers:
            if requirement["capability"] not in provider["capabilities"]:
                continue
            key = provider["availability_probe"]["key"]
            reasons: list[str] = []
            if key not in available_keys:
                reasons.append("unavailable")
            if not _platform_compatible(provider, selected_platform):
                reasons.append("platform_mismatch")
            compatible, io_reasons = _io_compatibility(provider, requirement)
            reasons.extend(io_reasons)
            row = {
                "provider_id": provider["provider_id"],
                "kind": provider["kind"],
                "available": key in available_keys,
                "platform_compatible": _platform_compatible(provider, selected_platform),
                "io_compatible": compatible,
                "priority": provider["priority"],
                "eligible": not reasons,
                "rejection_reasons": reasons,
            }
            rows.append(row)
            if not reasons:
                assessed.append((_rank_key(provider, requirement), provider, row))

        assessed.sort(key=lambda item: item[0])
        if assessed:
            _, winner, winner_row = assessed[0]
            winner_row["selected"] = True
            for _, _, candidate_row in assessed[1:]:
                candidate_row["selected"] = False
            selected.append(
                {
                    "requirement_id": requirement["requirement_id"],
                    "capability": requirement["capability"],
                    "provider_id": winner["provider_id"],
                    "kind": winner["kind"],
                    "accepts": winner["accepts"],
                    "produces": winner["produces"],
                    "invocation_hint": winner["invocation_hint"],
                    "output_adapter": winner["output_adapter"],
                    "license_status": winner["license_status"],
                }
            )
        else:
            reason = "unknown_capability" if requirement["capability"] not in known_capabilities else "no_available_compatible_provider"
            unresolved.append({**requirement, "reason": reason})
        candidate_groups.append(
            {
                "requirement_id": requirement["requirement_id"],
                "capability": requirement["capability"],
                "providers": sorted(rows, key=lambda item: item["provider_id"]),
            }
        )

    return {
        "schema_version": "1.0",
        "platform": selected_platform,
        "inventory_keys_considered": sorted(available_keys),
        "requirements": normalized,
        "selected": selected,
        "candidates": candidate_groups,
        "unresolved": unresolved,
        "execution_performed": False,
    }


def _requirements_from_file(path: Path) -> list[Any]:
    payload = load_json(path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("requirements"), list):
        return payload["requirements"]
    raise ValueError("requirements file must be a list or an object containing a requirements list")


def main(argv: list[str] | None = None) -> int:
    default_registry = Path(__file__).resolve().parents[1] / "references" / "executor-registry.yaml"
    parser = argparse.ArgumentParser(description="Resolve available providers; never invoke them.")
    parser.add_argument("--registry", type=Path, default=default_registry)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--platform")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        requirements: list[Any] = list(args.capability)
        if args.requirements:
            requirements.extend(_requirements_from_file(args.requirements))
        result = resolve_executors(
            load_json(args.registry),
            load_json(args.inventory),
            requirements,
            platform=args.platform,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if not result["unresolved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
