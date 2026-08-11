#!/usr/bin/env python3
"""Validate a versioned Research Mission and its cross-object references."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    DEFAULT_SCHEMA_PATH,
    autonomy_rank,
    load_document,
    load_json_yaml,
    load_registry,
    stable_hash,
    write_document,
)
from artifact_contract import validate_artifact  # noqa: E402


def _finding(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_cycle(steps: dict[str, dict[str, Any]]) -> bool:
    state: dict[str, int] = {}

    def visit(step_id: str) -> bool:
        marker = state.get(step_id, 0)
        if marker == 1:
            return True
        if marker == 2:
            return False
        state[step_id] = 1
        for dependency in steps[step_id].get("prerequisites", []):
            if dependency in steps and visit(dependency):
                return True
        state[step_id] = 2
        return False

    return any(visit(step_id) for step_id in steps if state.get(step_id, 0) == 0)


def validate(
    mission: Any,
    schema_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    schema_target = Path(schema_path) if schema_path else DEFAULT_SCHEMA_PATH
    schema = load_json_yaml(schema_target)
    registry_path = schema_target.parent / "capability-registry.yaml"
    if not registry_path.exists():
        registry_path = DEFAULT_REGISTRY_PATH
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if not isinstance(mission, dict):
        return {
            "valid": False,
            "schema_version": schema.get("mission_schema_version"),
            "mission_id": None,
            "errors": [_finding("type", "$", "Mission root must be an object")],
            "warnings": [],
            "counts": {},
        }

    enums = schema.get("enums", {})
    for field in schema.get("required_fields", []):
        if field not in mission or mission[field] is None or mission[field] == "":
            errors.append(_finding("required", field, "Required field is missing"))

    mission_version = mission.get("schema_version")
    if mission_version != schema.get("mission_schema_version"):
        if mission_version in schema.get("legacy_mission_schema_versions", []):
            errors.append(
                _finding(
                    "migration_required",
                    "schema_version",
                    "Legacy mission must be migrated with migrate_mission.py before validation or resume",
                )
            )
        else:
            errors.append(_finding("schema_version", "schema_version", "Unsupported mission schema version"))
    if not _nonempty(mission.get("mission_id")):
        errors.append(_finding("value", "mission_id", "mission_id must be a non-empty string"))
    if not _nonempty(mission.get("research_question")):
        errors.append(_finding("value", "research_question", "research_question must be a non-empty string"))

    domain = mission.get("domain")
    if domain not in enums.get("domains", []):
        errors.append(_finding("enum", "domain", f"Unknown domain: {domain}"))
    stage = mission.get("current_stage")
    if stage not in enums.get("stages", []):
        errors.append(_finding("enum", "current_stage", f"Unknown stage: {stage}"))
    status = mission.get("status")
    if status not in enums.get("mission_statuses", []):
        errors.append(_finding("enum", "status", f"Unknown mission status: {status}"))

    constraints = mission.get("constraints")
    if not isinstance(constraints, dict):
        errors.append(_finding("type", "constraints", "constraints must be an object"))
        constraints = {}
    ceiling = constraints.get("autonomy_ceiling")
    if ceiling not in enums.get("autonomy_levels", []):
        errors.append(_finding("enum", "constraints.autonomy_ceiling", f"Unknown autonomy ceiling: {ceiling}"))

    for field in schema.get("list_fields", []):
        if field in mission and not isinstance(mission[field], list):
            errors.append(_finding("type", field, "Field must be a list"))

    deliverables = mission.get("requested_deliverables", [])
    if isinstance(deliverables, list):
        if not deliverables:
            errors.append(_finding("minimum", "requested_deliverables", "At least one deliverable is required"))
        for index, item in enumerate(deliverables):
            if isinstance(item, str):
                if not item.strip():
                    errors.append(_finding("value", f"requested_deliverables[{index}]", "Deliverable cannot be empty"))
            elif not (isinstance(item, dict) and _nonempty(item.get("type"))):
                errors.append(_finding("type", f"requested_deliverables[{index}]", "Use a string or an object with a type"))

    id_fields = schema.get("id_fields", {})
    collection_ids: dict[str, set[str]] = {}
    for collection, id_field in id_fields.items():
        values = mission.get(collection, [])
        if not isinstance(values, list):
            continue
        seen: set[str] = set()
        for index, item in enumerate(values):
            if collection == "objectives" and isinstance(item, str):
                warnings.append(_finding("normalized_form", f"objectives[{index}]", "Planner should assign an objective_id"))
                continue
            if not isinstance(item, dict):
                errors.append(_finding("type", f"{collection}[{index}]", "Collection item must be an object"))
                continue
            identifier = item.get(id_field)
            if not _nonempty(identifier):
                errors.append(_finding("required", f"{collection}[{index}].{id_field}", "Stable ID is required"))
                continue
            if identifier in seen:
                errors.append(_finding("duplicate_id", f"{collection}[{index}].{id_field}", f"Duplicate ID: {identifier}"))
            seen.add(identifier)
        collection_ids[collection] = seen

    evidence_ids = collection_ids.get("evidence_records", set())
    evidence_by_id = {
        item.get("evidence_id"): item
        for item in mission.get("evidence_records", [])
        if isinstance(item, dict) and _nonempty(item.get("evidence_id"))
    }
    for index, claim in enumerate(mission.get("claims", [])):
        if not isinstance(claim, dict):
            continue
        claim_status = claim.get("status")
        if claim_status not in enums.get("claim_statuses", []):
            errors.append(_finding("enum", f"claims[{index}].status", f"Unknown claim status: {claim_status}"))
        linked = claim.get("evidence_ids", [])
        if not isinstance(linked, list):
            errors.append(_finding("type", f"claims[{index}].evidence_ids", "evidence_ids must be a list"))
            linked = []
        unknown = [identifier for identifier in linked if identifier not in evidence_ids]
        if unknown:
            errors.append(_finding("unknown_reference", f"claims[{index}].evidence_ids", "Unknown evidence IDs: " + ", ".join(unknown)))
        if claim_status == "supported":
            if not linked:
                errors.append(_finding("unsupported_claim", f"claims[{index}]", "Supported claim requires evidence IDs"))
            if not _nonempty(claim.get("source_anchor")) and not claim.get("source_anchors"):
                errors.append(_finding("missing_anchor", f"claims[{index}]", "Supported claim requires a source anchor"))
            for identifier in linked:
                record = evidence_by_id.get(identifier, {})
                if str(record.get("version_status", "")).lower() == "retracted":
                    errors.append(_finding("retracted_support", f"claims[{index}]", f"Retracted evidence cannot support an active claim: {identifier}"))

    for index, artifact in enumerate(mission.get("artifacts", [])):
        if not isinstance(artifact, dict):
            continue
        contract = validate_artifact(
            artifact,
            mission_id=mission.get("mission_id"),
            registry_path=registry_path,
            base_dir=base_dir,
        )
        for finding in contract["errors"]:
            errors.append(
                _finding(
                    f"artifact_{finding['code']}",
                    f"artifacts[{index}].{finding['path']}",
                    finding["message"],
                )
            )
        for finding in contract["warnings"]:
            warnings.append(
                _finding(
                    f"artifact_{finding['code']}",
                    f"artifacts[{index}].{finding['path']}",
                    finding["message"],
                )
            )

    for index, risk in enumerate(mission.get("risks", [])):
        if not isinstance(risk, dict):
            continue
        if risk.get("severity") not in enums.get("risk_severities", []):
            errors.append(_finding("enum", f"risks[{index}].severity", "Unknown risk severity"))
        if risk.get("status") not in enums.get("risk_statuses", []):
            errors.append(_finding("enum", f"risks[{index}].status", "Unknown risk status"))

    for index, decision in enumerate(mission.get("decisions", [])):
        if not isinstance(decision, dict):
            continue
        if decision.get("status") not in enums.get("decision_statuses", []):
            errors.append(_finding("enum", f"decisions[{index}].status", "Unknown decision status"))

    mission_gates = mission.get("mission_required_gates", mission.get("required_gates", []))
    if mission_gates is not None and not isinstance(mission_gates, list):
        errors.append(_finding("type", "mission_required_gates", "Mission gates must be a list"))
    elif isinstance(mission_gates, list):
        unknown_mission_gates = [gate for gate in mission_gates if gate not in enums.get("gates", [])]
        if unknown_mission_gates:
            errors.append(_finding("unknown_gate", "mission_required_gates", "Unknown gates: " + ", ".join(unknown_mission_gates)))

    route_contract = mission.get("route_contract")
    route_contract_sha256 = mission.get("route_contract_sha256")
    if route_contract is not None or route_contract_sha256 is not None:
        if not isinstance(route_contract, dict):
            errors.append(_finding("type", "route_contract", "route_contract must be an object"))
        elif route_contract_sha256 != stable_hash(route_contract):
            errors.append(
                _finding(
                    "route_contract_hash_mismatch",
                    "route_contract_sha256",
                    "route_contract_sha256 does not match the stored route_contract",
                )
            )

    for index, loop in enumerate(mission.get("open_loops", [])):
        if not isinstance(loop, dict):
            continue
        if loop.get("status") not in enums.get("open_loop_statuses", []):
            errors.append(_finding("enum", f"open_loops[{index}].status", "Unknown open-loop status"))

    for index, event in enumerate(mission.get("gate_ledger", [])):
        if not isinstance(event, dict):
            continue
        if event.get("gate") not in enums.get("gates", []):
            errors.append(_finding("unknown_gate", f"gate_ledger[{index}].gate", "Unknown gate"))
        if event.get("status") not in enums.get("gate_statuses", []):
            errors.append(_finding("enum", f"gate_ledger[{index}].status", "Unknown gate status"))

    try:
        allowed_skills = set(load_registry(registry_path))
    except ValueError as error:
        allowed_skills = set()
        errors.append(_finding("registry", "route", str(error)))

    route = mission.get("route", [])
    steps: dict[str, dict[str, Any]] = {}
    if isinstance(route, list):
        for index, step in enumerate(route):
            if not isinstance(step, dict):
                continue
            step_id = step.get("step_id")
            if _nonempty(step_id):
                steps[step_id] = step
            skill = step.get("skill")
            if skill not in allowed_skills:
                errors.append(_finding("unknown_skill", f"route[{index}].skill", f"Unknown skill: {skill}"))
            if step.get("stage") not in enums.get("stages", []):
                errors.append(_finding("unknown_stage", f"route[{index}].stage", f"Unknown stage: {step.get('stage')}"))
            if step.get("status") not in enums.get("route_statuses", []):
                errors.append(_finding("enum", f"route[{index}].status", f"Unknown route status: {step.get('status')}"))
            prerequisites = step.get("prerequisites", [])
            if not isinstance(prerequisites, list):
                errors.append(_finding("type", f"route[{index}].prerequisites", "prerequisites must be a list"))
            step_level = step.get("autonomy_level")
            if step_level not in enums.get("autonomy_levels", []):
                errors.append(_finding("enum", f"route[{index}].autonomy_level", f"Unknown autonomy level: {step_level}"))
            elif ceiling in enums.get("autonomy_levels", []) and autonomy_rank(step_level) > autonomy_rank(ceiling):
                errors.append(_finding("autonomy_ceiling", f"route[{index}].autonomy_level", "Step exceeds mission autonomy ceiling"))
            gates = step.get("required_gates", [])
            if not isinstance(gates, list):
                errors.append(_finding("type", f"route[{index}].required_gates", "required_gates must be a list"))
            else:
                unknown_gates = [gate for gate in gates if gate not in enums.get("gates", [])]
                if unknown_gates:
                    errors.append(_finding("unknown_gate", f"route[{index}].required_gates", "Unknown gates: " + ", ".join(unknown_gates)))
            expected_outputs = step.get("expected_outputs", [])
            required_output_groups = step.get("required_output_groups", [])
            optional_outputs = step.get("optional_outputs", [])
            if not isinstance(expected_outputs, list):
                errors.append(_finding("type", f"route[{index}].expected_outputs", "expected_outputs must be a list"))
                expected_outputs = []
            expected_tokens = {
                str(item.get("type") if isinstance(item, dict) else item).lower().replace("-", "_")
                for item in expected_outputs
            }
            if not isinstance(required_output_groups, list):
                errors.append(_finding("type", f"route[{index}].required_output_groups", "required_output_groups must be a list"))
            else:
                for group_index, group in enumerate(required_output_groups):
                    if not isinstance(group, list) or not group:
                        errors.append(_finding("type", f"route[{index}].required_output_groups[{group_index}]", "Each required output group must be a non-empty list"))
                        continue
                    unknown_outputs = [
                        item
                        for item in group
                        if str(item).lower().replace("-", "_") not in expected_tokens
                    ]
                    if unknown_outputs:
                        errors.append(
                            _finding(
                                "unknown_output",
                                f"route[{index}].required_output_groups[{group_index}]",
                                "Required outputs are not declared by the step: " + ", ".join(str(item) for item in unknown_outputs),
                            )
                        )
            if not isinstance(optional_outputs, list):
                errors.append(_finding("type", f"route[{index}].optional_outputs", "optional_outputs must be a list"))

    for step_id, step in steps.items():
        for dependency in step.get("prerequisites", []):
            if dependency not in steps:
                errors.append(_finding("unknown_dependency", f"route.{step_id}.prerequisites", f"Unknown step ID: {dependency}"))
            if dependency == step_id:
                errors.append(_finding("self_dependency", f"route.{step_id}.prerequisites", "A step cannot depend on itself"))
    if steps and _has_cycle(steps):
        errors.append(_finding("cycle", "route", "Route dependency graph contains a cycle"))

    for index, artifact in enumerate(mission.get("artifacts", [])):
        if not isinstance(artifact, dict):
            continue
        provenance = artifact.get("provenance") if isinstance(artifact.get("provenance"), dict) else {}
        producer = provenance.get("producer")
        producer_step_id = provenance.get("producer_step_id")
        if producer not in allowed_skills:
            continue
        if producer_step_id not in steps:
            errors.append(
                _finding(
                    "unknown_producer_step",
                    f"artifacts[{index}].provenance.producer_step_id",
                    f"Artifact producer step is absent from route: {producer_step_id}",
                )
            )
        elif steps[producer_step_id].get("skill") != producer:
            errors.append(
                _finding(
                    "producer_step_mismatch",
                    f"artifacts[{index}].provenance.producer_step_id",
                    f"Route step {producer_step_id} belongs to {steps[producer_step_id].get('skill')}, not {producer}",
                )
            )

    counts = {
        field: len(mission.get(field, []))
        for field in schema.get("list_fields", [])
        if isinstance(mission.get(field, []), list)
    }
    return {
        "valid": not errors,
        "schema_version": schema.get("mission_schema_version"),
        "mission_id": mission.get("mission_id"),
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    input_path = args.input.resolve()
    result = validate(
        load_document(input_path),
        args.schema,
        base_dir=args.base_dir or input_path.parent,
    )
    if args.output:
        write_document(args.output, result)
    else:
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
