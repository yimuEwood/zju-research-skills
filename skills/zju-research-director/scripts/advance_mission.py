#!/usr/bin/env python3
"""Apply a validated, resumable route-state transition to a Research Mission."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import load_document, stable_hash, write_document  # noqa: E402
from check_stage_gate import check_gate  # noqa: E402
from artifact_contract import validate_artifact  # noqa: E402
from plan_mission import audit_route_contract  # noqa: E402
from validate_mission import validate  # noqa: E402


ALLOWED_TRANSITIONS = {
    "planned": {"ready", "blocked", "skipped"},
    "ready": {"running", "completed", "blocked", "skipped"},
    "running": {"completed", "blocked"},
    "blocked": {"ready", "skipped"},
    "completed": {"completed"},
    "skipped": {"skipped"},
}


def _token(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _gate_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        return value["results"]
    if isinstance(value, dict) and "gate" in value:
        return [value]
    raise ValueError("gate_results must be a result object, list, or object containing results")


def _next_id(existing: set[str], prefix: str) -> str:
    index = 1
    while f"{prefix}-{index:03d}" in existing:
        index += 1
    return f"{prefix}-{index:03d}"


def _checked_state_hash(mission: dict[str, Any]) -> str:
    payload = copy.deepcopy(mission)
    payload.pop("gate_ledger", None)
    payload.pop("state_hash", None)
    return stable_hash(payload)


def advance(
    mission: dict[str, Any],
    step_id: str,
    target_status: str,
    artifact_ids: list[str] | None = None,
    gate_results: Any = None,
    reason: str | None = None,
    base_dir: str | Path | None = None,
    trusted_receipts: list[dict[str, Any]] | None = None,
    trusted_validation_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(mission, dict):
        raise ValueError("Mission root must be an object")
    pre_validation = validate(mission, base_dir=base_dir)
    if not pre_validation["valid"]:
        return {
            "valid": False,
            "mission": copy.deepcopy(mission),
            "errors": [{"code": "pre_transition_validation", "details": pre_validation["errors"]}],
        }
    if mission.get("route_contract") is not None or mission.get("route_contract_sha256") is not None:
        contract_audit = audit_route_contract(mission)
        if not contract_audit["valid"]:
            return {
                "valid": False,
                "mission": copy.deepcopy(mission),
                "errors": [
                    {
                        "code": "route_contract_mismatch",
                        "message": "Mission route differs from canonical requested-deliverable policy",
                        "details": contract_audit["issues"],
                    }
                ],
            }
    artifact_ids = list(dict.fromkeys(artifact_ids or []))
    results = _gate_list(gate_results)
    route = mission.get("route", [])
    if not isinstance(route, list):
        raise ValueError("mission.route must be a list")
    index = next((offset for offset, step in enumerate(route) if isinstance(step, dict) and step.get("step_id") == step_id), None)
    if index is None:
        return {"valid": False, "mission": copy.deepcopy(mission), "errors": [{"code": "unknown_step", "message": f"Unknown step: {step_id}"}]}

    step = route[index]
    source_status = step.get("status")
    errors: list[dict[str, Any]] = []
    if target_status != "completed" and results:
        errors.append(
            {
                "code": "unexpected_gate_results",
                "message": "Gate results are accepted only for a completed transition, where they are recomputed locally",
            }
        )
    if target_status not in ALLOWED_TRANSITIONS.get(source_status, set()):
        errors.append({"code": "invalid_transition", "message": f"Cannot transition {step_id} from {source_status} to {target_status}"})

    route_by_id = {item.get("step_id"): item for item in route if isinstance(item, dict)}
    incomplete = [
        dependency
        for dependency in step.get("prerequisites", [])
        if route_by_id.get(dependency, {}).get("status") != "completed"
    ]
    if target_status in {"ready", "running", "completed"} and incomplete:
        errors.append({"code": "incomplete_prerequisite", "message": "Incomplete prerequisites: " + ", ".join(incomplete)})
    if source_status == "blocked" and target_status == "ready":
        unresolved_step_loops = [
            loop.get("loop_id", "UNKNOWN")
            for loop in mission.get("open_loops", [])
            if isinstance(loop, dict)
            and loop.get("step_id") == step_id
            and loop.get("blocking")
            and loop.get("status") != "resolved"
        ]
        if unresolved_step_loops:
            errors.append({"code": "unresolved_blocker", "message": "Resolve step open loops before resuming: " + ", ".join(unresolved_step_loops)})

    artifacts = {
        item.get("artifact_id"): item
        for item in mission.get("artifacts", [])
        if isinstance(item, dict) and isinstance(item.get("artifact_id"), str)
    }
    if target_status == "completed":
        missing_artifacts = [identifier for identifier in artifact_ids if identifier not in artifacts]
        if missing_artifacts:
            errors.append({"code": "unknown_artifact", "message": "Unknown artifact IDs: " + ", ".join(missing_artifacts)})
        unvalidated = [identifier for identifier in artifact_ids if artifacts.get(identifier, {}).get("status") != "validated"]
        if unvalidated:
            errors.append({"code": "unvalidated_artifact", "message": "Artifacts are not validated: " + ", ".join(unvalidated)})
        invalid_contracts = {}
        for identifier in artifact_ids:
            if identifier not in artifacts:
                continue
            contract = validate_artifact(
                artifacts[identifier],
                mission_id=mission.get("mission_id"),
                base_dir=base_dir,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            if not contract["valid"]:
                invalid_contracts[identifier] = contract["errors"]
            elif not contract.get("content_verified") or not contract.get("validation_receipt_verified"):
                invalid_contracts[identifier] = [
                    {
                        "code": "trusted_validation_attestation",
                        "path": "validation",
                        "message": "Completed step requires a caller-supplied independent trusted-runner attestation",
                    }
                ]
        if invalid_contracts:
            errors.append(
                {
                    "code": "invalid_artifact_contract",
                    "message": "Completed step requires artifacts that satisfy the shared envelope",
                    "findings": invalid_contracts,
                }
            )
        wrong_producer = []
        for identifier in artifact_ids:
            artifact = artifacts.get(identifier, {})
            provenance = artifact.get("provenance", {}) if isinstance(artifact.get("provenance"), dict) else {}
            if provenance.get("producer") != step.get("skill") or provenance.get("producer_step_id") != step_id:
                wrong_producer.append(identifier)
        if wrong_producer:
            errors.append(
                {
                    "code": "artifact_step_mismatch",
                    "message": "Artifacts must identify the current specialist and producer_step_id: " + ", ".join(wrong_producer),
                }
            )
        required_groups = step.get("required_output_groups")
        if not isinstance(required_groups, list):
            legacy = step.get("expected_outputs", [])
            required_groups = [legacy] if legacy else []
        if required_groups and not artifact_ids:
            errors.append({"code": "missing_output", "message": "Completed step must identify at least one validated artifact"})
        actual_types = {_token(artifacts[identifier].get("artifact_type")) for identifier in artifact_ids if identifier in artifacts}
        normalized_groups = [
            {
                _token(item.get("type")) if isinstance(item, dict) else _token(item)
                for item in group
            }
            for group in required_groups
            if isinstance(group, list)
        ]
        missing_output_groups = [sorted(group) for group in normalized_groups if group and not group.intersection(actual_types)]
        if missing_output_groups:
            errors.append(
                {
                    "code": "missing_required_output",
                    "message": "Validated artifacts do not satisfy every required output group",
                    "missing_groups": missing_output_groups,
                    "actual": sorted(actual_types),
                }
            )
        by_gate = {result.get("gate"): result for result in results if isinstance(result, dict)}
        missing_gates = [gate for gate in step.get("required_gates", []) if gate not in by_gate]
        if missing_gates:
            errors.append({"code": "missing_gate_result", "message": "Missing gate results: " + ", ".join(missing_gates)})
        recomputed = {
            gate: check_gate(
                mission,
                {"gate": gate, "artifact_ids": artifact_ids} if gate == "artifact" else gate,
                base_dir=base_dir,
                trusted_receipts=trusted_receipts,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            for gate in step.get("required_gates", [])
        }
        mismatched_gates = [
            gate
            for gate in step.get("required_gates", [])
            if gate in by_gate and by_gate[gate].get("status") != recomputed[gate].get("status")
        ]
        if mismatched_gates:
            errors.append({"code": "gate_result_mismatch", "message": "Supplied gate status differs from deterministic check: " + ", ".join(mismatched_gates)})
        failed_gates = [gate for gate, result in recomputed.items() if result.get("status") != "passed"]
        if failed_gates:
            errors.append({"code": "gate_not_passed", "message": "Gates did not pass: " + ", ".join(failed_gates)})
        if not missing_gates and not mismatched_gates and not failed_gates:
            results = [recomputed[gate] for gate in step.get("required_gates", [])]
    if target_status in {"blocked", "skipped"} and not reason:
        errors.append({"code": "missing_reason", "message": f"{target_status} transition requires a reason"})
    if errors:
        return {"valid": False, "mission": copy.deepcopy(mission), "errors": errors}

    updated = copy.deepcopy(mission)
    updated.setdefault("gate_ledger", [])
    gate_ids = {
        event.get("gate_event_id")
        for event in updated["gate_ledger"]
        if isinstance(event, dict)
    }

    def append_gate_event(result: dict[str, Any], event_step_id: str | None, checked_hash: str) -> None:
        if not isinstance(result, dict) or not result.get("gate"):
            return
        event = {
            "gate_event_id": _next_id(gate_ids, f"GATE-{event_step_id or 'MISSION'}"),
            "step_id": event_step_id,
            "gate": result.get("gate"),
            "status": result.get("status"),
            "checked_state_sha256": checked_hash,
            "checked_inputs": copy.deepcopy(result.get("checked_inputs", [])),
            "blockers": copy.deepcopy(result.get("blockers", [])),
            "warnings": copy.deepcopy(result.get("warnings", [])),
            "decision_id": result.get("decision_id"),
        }
        if result.get("authorization_state_sha256"):
            event["authorization_state_sha256"] = result["authorization_state_sha256"]
        gate_ids.add(event["gate_event_id"])
        updated["gate_ledger"].append(event)

    stage_checked_hash = _checked_state_hash(mission)
    for result in results:
        append_gate_event(result, step_id, stage_checked_hash)

    target_step = updated["route"][index]
    target_step["status"] = target_status
    if artifact_ids:
        target_step["produced_artifact_ids"] = list(
            dict.fromkeys(target_step.get("produced_artifact_ids", []) + artifact_ids)
        )

    if target_status == "blocked":
        updated["status"] = "blocked"
        updated.setdefault("open_loops", [])
        loop_ids = {loop.get("loop_id") for loop in updated["open_loops"] if isinstance(loop, dict)}
        updated["open_loops"].append(
            {
                "loop_id": _next_id(loop_ids, f"LOOP-{step_id}"),
                "type": "state_transition",
                "step_id": step_id,
                "issue": reason,
                "owner": "research_owner",
                "status": "blocked",
                "blocking": True,
            }
        )
        updated["next_action"] = {"action": "resolve_blocking_open_loop", "step_id": step_id}
    else:
        completed_ids = {
            item.get("step_id")
            for item in updated["route"]
            if isinstance(item, dict) and item.get("status") in {"completed", "skipped"}
        }
        for candidate in updated["route"]:
            if candidate.get("status") == "planned" and set(candidate.get("prerequisites", [])).issubset(completed_ids):
                candidate["status"] = "ready"
        ready = [candidate for candidate in updated["route"] if candidate.get("status") == "ready"]
        all_completed = bool(updated["route"]) and all(
            candidate.get("status") in {"completed", "skipped"}
            for candidate in updated["route"]
        )
        mission_gate_results = []
        if all_completed:
            mission_gate_results = [
                check_gate(
                    updated,
                    gate,
                    base_dir=base_dir,
                    trusted_receipts=trusted_receipts,
                    trusted_validation_receipts=trusted_validation_receipts,
                )
                for gate in updated.get("mission_required_gates", [])
            ]
            mission_checked_hash = _checked_state_hash(updated)
            for result in mission_gate_results:
                append_gate_event(result, None, mission_checked_hash)
        missing_mission_gates = [
            result.get("gate")
            for result in mission_gate_results
            if result.get("status") != "passed"
        ]
        if all_completed and not missing_mission_gates:
            updated["status"] = "completed"
            updated["next_action"] = {"action": "mission_complete"}
        elif all_completed:
            updated["status"] = "active"
            updated["next_action"] = {"action": "evaluate_mission_gates", "gates": missing_mission_gates}
        elif ready:
            updated["status"] = "active"
            updated["current_stage"] = ready[0]["stage"]
            updated["next_action"] = {"action": "execute_ready_step", "step_id": ready[0]["step_id"], "skill": ready[0]["skill"]}
        else:
            updated["status"] = "blocked"
            updated["next_action"] = {"action": "inspect_route_state"}

    updated["revision"] = int(updated.get("revision", 1)) + 1
    hash_input = copy.deepcopy(updated)
    hash_input.pop("state_hash", None)
    updated["state_hash"] = stable_hash(hash_input)
    validation = validate(updated, base_dir=base_dir)
    if not validation["valid"]:
        return {"valid": False, "mission": copy.deepcopy(mission), "errors": [{"code": "post_transition_validation", "details": validation["errors"]}]}
    return {
        "valid": True,
        "mission": updated,
        "transition": {"step_id": step_id, "from": source_status, "to": target_status},
        "errors": [],
        "validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--step", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--artifact-id", action="append", default=[])
    parser.add_argument("--gate-results", type=Path)
    parser.add_argument("--reason")
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument("--approval-receipts", type=Path)
    parser.add_argument(
        "--trusted-validation-receipts",
        type=Path,
        help="attestations from a caller-authenticated independent validation runner",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    gate_results = load_document(args.gate_results) if args.gate_results else None
    trusted_receipts = None
    if args.approval_receipts:
        receipt_document = load_document(args.approval_receipts)
        trusted_receipts = (
            receipt_document.get("receipts")
            if isinstance(receipt_document, dict)
            else receipt_document
        )
        if not isinstance(trusted_receipts, list):
            raise ValueError("approval receipt document must be a list or an object with receipts")
    trusted_validation_receipts = None
    if args.trusted_validation_receipts:
        receipt_document = load_document(args.trusted_validation_receipts)
        trusted_validation_receipts = (
            receipt_document.get("attestations")
            if isinstance(receipt_document, dict)
            else receipt_document
        )
        if not isinstance(trusted_validation_receipts, list):
            raise ValueError(
                "trusted validation receipt document must be a list or an object with attestations"
            )
    mission_path = args.mission.resolve()
    result = advance(
        load_document(mission_path),
        args.step,
        args.status,
        args.artifact_id,
        gate_results,
        args.reason,
        base_dir=args.base_dir or mission_path.parent,
        trusted_receipts=trusted_receipts,
        trusted_validation_receipts=trusted_validation_receipts,
    )
    write_document(args.output, result["mission"] if result["valid"] else result)
    print(json.dumps({key: result.get(key) for key in ("valid", "transition", "errors")}, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
