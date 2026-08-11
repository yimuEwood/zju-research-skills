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


def advance(
    mission: dict[str, Any],
    step_id: str,
    target_status: str,
    artifact_ids: list[str] | None = None,
    gate_results: Any = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if not isinstance(mission, dict):
        raise ValueError("Mission root must be an object")
    pre_validation = validate(mission)
    if not pre_validation["valid"]:
        return {
            "valid": False,
            "mission": copy.deepcopy(mission),
            "errors": [{"code": "pre_transition_validation", "details": pre_validation["errors"]}],
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
        if step.get("expected_outputs") and not artifact_ids:
            errors.append({"code": "missing_output", "message": "Completed step must identify at least one validated artifact"})
        expected_types = {
            _token(item.get("type")) if isinstance(item, dict) else _token(item)
            for item in step.get("expected_outputs", [])
        }
        actual_types = {_token(artifacts[identifier].get("artifact_type")) for identifier in artifact_ids if identifier in artifacts}
        if expected_types and actual_types and not expected_types.intersection(actual_types):
            errors.append(
                {
                    "code": "output_type_mismatch",
                    "message": "Validated artifacts do not match any declared expected output type",
                    "expected": sorted(expected_types),
                    "actual": sorted(actual_types),
                }
            )
        by_gate = {result.get("gate"): result for result in results if isinstance(result, dict)}
        missing_gates = [gate for gate in step.get("required_gates", []) if gate not in by_gate]
        if missing_gates:
            errors.append({"code": "missing_gate_result", "message": "Missing gate results: " + ", ".join(missing_gates)})
        recomputed = {
            gate: check_gate(mission, {"gate": gate, "artifact_ids": artifact_ids} if gate == "artifact" else gate)
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
    for result in results:
        if not isinstance(result, dict) or not result.get("gate"):
            continue
        event = {
            "gate_event_id": _next_id(gate_ids, f"GATE-{step_id}"),
            "step_id": step_id,
            "gate": result.get("gate"),
            "status": result.get("status"),
            "checked_inputs": copy.deepcopy(result.get("checked_inputs", [])),
            "blockers": copy.deepcopy(result.get("blockers", [])),
            "warnings": copy.deepcopy(result.get("warnings", [])),
            "decision_id": result.get("decision_id"),
        }
        gate_ids.add(event["gate_event_id"])
        updated["gate_ledger"].append(event)

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
            if isinstance(item, dict) and item.get("status") == "completed"
        }
        for candidate in updated["route"]:
            if candidate.get("status") == "planned" and set(candidate.get("prerequisites", [])).issubset(completed_ids):
                candidate["status"] = "ready"
        ready = [candidate for candidate in updated["route"] if candidate.get("status") == "ready"]
        all_completed = bool(updated["route"]) and all(candidate.get("status") == "completed" for candidate in updated["route"])
        latest_gate_status = {
            event.get("gate"): event.get("status")
            for event in updated["gate_ledger"]
            if isinstance(event, dict)
        }
        missing_mission_gates = [
            gate for gate in updated.get("mission_required_gates", []) if latest_gate_status.get(gate) != "passed"
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
    validation = validate(updated)
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
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    gate_results = load_document(args.gate_results) if args.gate_results else None
    result = advance(load_document(args.mission), args.step, args.status, args.artifact_id, gate_results, args.reason)
    write_document(args.output, result["mission"] if result["valid"] else result)
    print(json.dumps({key: result.get(key) for key in ("valid", "transition", "errors")}, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
\n