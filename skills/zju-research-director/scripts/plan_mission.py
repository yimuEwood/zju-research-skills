#!/usr/bin/env python3
"""Build a deterministic capability DAG for a structured Research Mission."""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import (  # noqa: E402
    autonomy_rank,
    load_document,
    load_registry,
    stable_hash,
    write_document,
)
from validate_mission import validate  # noqa: E402


PLANNER_VERSION = "1.0"
STAGE_ORDER = {
    "framing": 0,
    "discovery": 1,
    "access": 2,
    "reading": 3,
    "synthesis": 4,
    "design": 5,
    "execution": 6,
    "analysis": 7,
    "communication": 8,
    "review": 9,
    "sharing": 10,
    "commercialization": 11,
    "integrity": 12,
}

SKILL_STAGE = {
    "zju-literature-search": "discovery",
    "zju-literature-monitor": "discovery",
    "zju-chemistry-databases": "discovery",
    "zju-fulltext-access": "access",
    "zju-paper-reader": "reading",
    "zju-evidence-synthesis": "synthesis",
    "zju-hypothesis-design": "design",
    "zju-experiment-log": "execution",
    "zju-statistics-audit": "analysis",
    "zju-reference-audit": "review",
    "zju-scientific-figure": "communication",
    "zju-scientific-writing": "communication",
    "zju-paper2ppt": "communication",
    "zju-proposal-writer": "communication",
    "zju-reviewer": "review",
    "zju-review-response": "communication",
    "zju-data-availability": "sharing",
    "zju-paper-to-patent": "commercialization",
    "zju-research-integrity": "integrity",
}

SKILL_DEPENDENCIES = {
    "zju-fulltext-access": ["zju-literature-search"],
    "zju-paper-reader": ["zju-fulltext-access"],
    "zju-literature-monitor": ["zju-literature-search"],
    "zju-evidence-synthesis": ["zju-literature-search", "zju-paper-reader"],
    "zju-hypothesis-design": ["zju-evidence-synthesis"],
    "zju-statistics-audit": ["zju-experiment-log"],
    "zju-reference-audit": ["zju-literature-search"],
    "zju-scientific-figure": ["zju-statistics-audit"],
    "zju-scientific-writing": ["zju-evidence-synthesis", "zju-statistics-audit"],
    "zju-paper2ppt": ["zju-paper-reader"],
    "zju-proposal-writer": ["zju-evidence-synthesis", "zju-hypothesis-design"],
    "zju-reviewer": ["zju-scientific-writing", "zju-proposal-writer", "zju-scientific-figure", "zju-statistics-audit", "zju-reference-audit", "zju-review-response"],
    "zju-data-availability": ["zju-experiment-log", "zju-scientific-writing"],
    "zju-paper-to-patent": ["zju-literature-search", "zju-paper-reader"],
}

SKILL_GATES = {
    "zju-literature-search": ["intake"],
    "zju-literature-monitor": ["evidence"],
    "zju-fulltext-access": ["fulltext"],
    "zju-reference-audit": ["reference"],
    "zju-paper-reader": ["fulltext", "evidence"],
    "zju-evidence-synthesis": ["evidence"],
    "zju-hypothesis-design": ["evidence", "claim"],
    "zju-experiment-log": ["data", "integrity"],
    "zju-statistics-audit": ["data", "analysis"],
    "zju-scientific-figure": ["analysis", "artifact"],
    "zju-scientific-writing": ["reference", "claim", "artifact"],
    "zju-paper2ppt": ["evidence", "artifact"],
    "zju-reviewer": ["artifact", "integrity"],
    "zju-review-response": ["artifact"],
    "zju-data-availability": ["reproducibility", "artifact"],
    "zju-proposal-writer": ["evidence", "claim", "artifact"],
    "zju-paper-to-patent": ["evidence", "patent_legal"],
    "zju-chemistry-databases": ["evidence"],
    "zju-research-integrity": ["integrity"],
}

ROUTE_RECIPES = {
    "research_plan": ["zju-literature-search", "zju-evidence-synthesis", "zju-hypothesis-design"],
    "search_protocol": ["zju-literature-search"],
    "literature_set": ["zju-literature-search"],
    "literature_monitor": ["zju-literature-search", "zju-literature-monitor"],
    "full_text": ["zju-fulltext-access"],
    "paper_card": ["zju-fulltext-access", "zju-paper-reader"],
    "bilingual_reader": ["zju-fulltext-access", "zju-paper-reader"],
    "evidence_synthesis": ["zju-literature-search", "zju-fulltext-access", "zju-paper-reader", "zju-evidence-synthesis"],
    "systematic_review": ["zju-literature-search", "zju-fulltext-access", "zju-paper-reader", "zju-evidence-synthesis"],
    "hypothesis_set": ["zju-literature-search", "zju-evidence-synthesis", "zju-hypothesis-design"],
    "experiment_design": ["zju-literature-search", "zju-evidence-synthesis", "zju-hypothesis-design"],
    "failed_experiment_redesign": ["zju-experiment-log", "zju-statistics-audit", "zju-evidence-synthesis", "zju-hypothesis-design"],
    "experiment_log": ["zju-experiment-log"],
    "statistics_audit": ["zju-statistics-audit"],
    "scientific_figure": ["zju-statistics-audit", "zju-scientific-figure"],
    "manuscript": ["zju-statistics-audit", "zju-reference-audit", "zju-scientific-writing", "zju-reviewer", "zju-research-integrity"],
    "raw_data_to_manuscript": ["zju-experiment-log", "zju-statistics-audit", "zju-scientific-figure", "zju-reference-audit", "zju-scientific-writing", "zju-reviewer", "zju-research-integrity"],
    "presentation": ["zju-fulltext-access", "zju-paper-reader", "zju-paper2ppt"],
    "peer_review": ["zju-reviewer"],
    "review_response": ["zju-review-response"],
    "data_availability": ["zju-data-availability"],
    "proposal": ["zju-literature-search", "zju-evidence-synthesis", "zju-hypothesis-design", "zju-reference-audit", "zju-proposal-writer"],
    "patent_triage": ["zju-literature-search", "zju-fulltext-access", "zju-paper-reader", "zju-paper-to-patent"],
    "technical_disclosure": ["zju-paper-reader", "zju-paper-to-patent"],
    "chemistry_database_record": ["zju-chemistry-databases"],
    "integrity_audit": ["zju-research-integrity"],
    "retraction_update": ["zju-literature-monitor", "zju-reference-audit", "zju-evidence-synthesis", "zju-scientific-writing"],
    "submission_package": ["zju-statistics-audit", "zju-reference-audit", "zju-scientific-figure", "zju-scientific-writing", "zju-reviewer", "zju-data-availability", "zju-research-integrity"],
}

DELIVERABLE_ALIASES = {
    "literature_search": "literature_set",
    "evidence_table": "literature_set",
    "fulltext": "full_text",
    "full_text_access": "full_text",
    "deep_reading": "paper_card",
    "paper_reader": "paper_card",
    "review": "peer_review",
    "reviewer_report": "peer_review",
    "response_letter": "review_response",
    "ppt": "presentation",
    "pptx": "presentation",
    "slide_deck": "presentation",
    "research_proposal": "proposal",
    "grant_proposal": "proposal",
    "patent": "patent_triage",
    "patent_disclosure": "technical_disclosure",
    "data_statement": "data_availability",
    "manuscript_package": "submission_package",
    "complete_submission": "submission_package",
    "figure": "scientific_figure",
    "statistical_audit": "statistics_audit",
    "hypotheses": "hypothesis_set",
    "experiment_plan": "experiment_design",
}

SKILL_OUTPUT_TYPES = {
    "zju-literature-search": {"search_protocol", "literature_set", "evidence_table"},
    "zju-literature-monitor": {"literature_monitor", "monitor_update"},
    "zju-fulltext-access": {"full_text", "fulltext_pdf"},
    "zju-reference-audit": {"reference_audit"},
    "zju-paper-reader": {"paper_card", "bilingual_reader", "reader_notes"},
    "zju-evidence-synthesis": {"evidence_synthesis", "systematic_review"},
    "zju-hypothesis-design": {"hypothesis_set", "experiment_design"},
    "zju-experiment-log": {"experiment_log", "run_manifest"},
    "zju-statistics-audit": {"statistics_audit", "analysis_audit"},
    "zju-scientific-figure": {"scientific_figure", "figure_manifest"},
    "zju-scientific-writing": {"manuscript", "manuscript_section"},
    "zju-paper2ppt": {"presentation", "pptx", "deck_plan"},
    "zju-reviewer": {"peer_review", "reviewer_report"},
    "zju-review-response": {"review_response", "response_letter"},
    "zju-data-availability": {"data_availability", "data_statement"},
    "zju-proposal-writer": {"proposal", "research_proposal"},
    "zju-paper-to-patent": {"patent_triage", "technical_disclosure"},
    "zju-chemistry-databases": {"chemistry_database_record"},
    "zju-research-integrity": {"integrity_audit"},
}

GATE_ALIASES = {
    "human": "external_action",
    "credentialed_access": "external_action",
    "interactive_authentication": "external_action",
    "legal_review": "patent_legal",
    "patent_review": "patent_legal",
    "ethics_approval": "ethics",
    "privacy_review": "privacy",
    "release": "submission_release",
    "submission": "submission_release",
}


def _token(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


def _deliverable_type(item: Any) -> str:
    raw = item.get("type", "") if isinstance(item, dict) else str(item)
    token = _token(raw)
    return DELIVERABLE_ALIASES.get(token, token)


def _normalize_objectives(values: Any, question: str) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        values = [question]
    result: list[dict[str, Any]] = []
    for index, value in enumerate(values, 1):
        if isinstance(value, str):
            result.append({"objective_id": f"O{index:02d}", "statement": value})
        elif isinstance(value, dict):
            item = copy.deepcopy(value)
            item.setdefault("objective_id", f"O{index:02d}")
            if "statement" not in item and "objective" in item:
                item["statement"] = item.pop("objective")
            result.append(item)
    return result


def _validated_artifact_types(mission: dict[str, Any]) -> set[str]:
    return {
        _token(str(artifact.get("artifact_type", "")))
        for artifact in mission.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("status") == "validated"
    }


def _registry_output_index(registry: dict[str, dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for skill, entry in registry.items():
        for output in entry.get("produces", []):
            if isinstance(output, str):
                index.setdefault(_token(output), skill)
            elif isinstance(output, dict) and isinstance(output.get("type"), str):
                index.setdefault(_token(output["type"]), skill)
    return index


def _topological_order(selected: set[str], dependencies: dict[str, set[str]]) -> list[str]:
    ordered: list[str] = []
    remaining = set(selected)
    while remaining:
        ready = [skill for skill in remaining if dependencies[skill].issubset(ordered)]
        if not ready:
            ready = list(remaining)
        ready.sort(key=lambda skill: (STAGE_ORDER.get(SKILL_STAGE.get(skill, "integrity"), 99), skill))
        chosen = ready[0]
        ordered.append(chosen)
        remaining.remove(chosen)
    return ordered


def _unique_loop_id(mission: dict[str, Any], stem: str) -> str:
    existing = {
        loop.get("loop_id")
        for loop in mission.get("open_loops", [])
        if isinstance(loop, dict)
    }
    index = 1
    while f"LOOP-{stem}-{index:03d}" in existing:
        index += 1
    return f"LOOP-{stem}-{index:03d}"


def _append_open_loop(mission: dict[str, Any], stem: str, issue: str, blocking: bool = True) -> None:
    if any(isinstance(loop, dict) and loop.get("issue") == issue and loop.get("status") != "resolved" for loop in mission["open_loops"]):
        return
    mission["open_loops"].append(
        {
            "loop_id": _unique_loop_id(mission, stem),
            "type": "routing",
            "issue": issue,
            "owner": "research_owner",
            "status": "blocked" if blocking else "open",
            "blocking": blocking,
        }
    )


def _normalize_gate(gate: str) -> str:
    token = _token(gate)
    return GATE_ALIASES.get(token, token)


def plan_mission(mission: dict[str, Any], registry_path: str | Path | None = None) -> dict[str, Any]:
    if not isinstance(mission, dict):
        raise ValueError("Mission root must be an object")
    planned = copy.deepcopy(mission)
    intake_facts = planned.get("intake_facts") if isinstance(planned.get("intake_facts"), dict) else {}
    question = str(
        planned.get("research_question")
        or intake_facts.get("research_question")
        or planned.get("problem_statement")
        or planned.get("request")
        or planned.get("prompt")
        or "TO_BE_FRAMED"
    ).strip()
    planned.setdefault("schema_version", "1.0")
    planned.setdefault("mission_id", "MISSION-UNASSIGNED")
    planned["research_question"] = question
    planned.setdefault("domain", "general")
    planned["objectives"] = _normalize_objectives(planned.get("objectives"), question)
    constraints = planned.setdefault("constraints", {})
    if isinstance(constraints, list):
        constraints = {"notes": copy.deepcopy(constraints)}
        planned["constraints"] = constraints
    elif not isinstance(constraints, dict):
        constraints = {}
        planned["constraints"] = constraints
    constraints.setdefault("autonomy_ceiling", planned.pop("autonomy_ceiling", "L2"))
    risk_triggers = planned.get("risk_triggers", []) if isinstance(planned.get("risk_triggers", []), list) else []
    trigger_flags = {
        "credentialed_access": "credentialed_access",
        "human_participants": "ethics_required",
        "sensitive_data": "sensitive_data",
        "patent_output": "patent_output",
        "submission": "release_intent",
        "unattended_loop": "unattended_loop_requested",
        "raw_data": "raw_data_supplied",
        "regulated_material": "regulated_material",
        "retraction_or_correction": "retraction_or_correction",
    }
    for trigger in risk_triggers:
        if trigger in trigger_flags:
            constraints.setdefault(trigger_flags[trigger], True)
    if isinstance(planned.get("forbidden_actions"), list):
        constraints.setdefault("forbidden_actions", copy.deepcopy(planned["forbidden_actions"]))
    planned.setdefault("requested_deliverables", ["research_plan"])
    planned.setdefault("current_stage", "framing")
    planned.setdefault("status", "draft")
    for field in ("evidence_records", "claims", "hypotheses", "experiments", "artifacts", "decisions", "risks", "open_loops", "gate_ledger"):
        if not isinstance(planned.get(field), list):
            planned[field] = []

    registry = load_registry(registry_path)
    registry_outputs = _registry_output_index(registry)
    deliverable_types = [_deliverable_type(item) for item in planned.get("requested_deliverables", [])]
    selected: set[str] = set()
    planning_errors: list[dict[str, str]] = []
    unresolved: list[str] = []
    required = planned.get("required_skills", constraints.get("required_skills", []))
    explicit_required = isinstance(required, list) and bool(required)
    for deliverable in deliverable_types:
        recipe = ROUTE_RECIPES.get(deliverable)
        if recipe:
            selected.update(recipe)
            continue
        registry_skill = registry_outputs.get(deliverable)
        if registry_skill:
            selected.add(registry_skill)
        elif explicit_required:
            custom = planned.setdefault("custom_deliverables", [])
            if deliverable not in custom:
                custom.append(deliverable)
        else:
            unresolved.append(deliverable)
            _append_open_loop(planned, "ROUTE", f"No registered capability produces requested deliverable: {deliverable}")

    forbidden = set(planned.get("forbidden_skills", constraints.get("forbidden_skills", [])))
    if not isinstance(required, list):
        required = []
        planning_errors.append({"code": "required_skills_type", "message": "required_skills must be a list"})
    if not isinstance(forbidden, set):
        forbidden = set(forbidden) if isinstance(forbidden, list) else set()
    for skill in required:
        if skill not in registry:
            planning_errors.append({"code": "unknown_required_skill", "message": f"Unknown required skill: {skill}"})
        else:
            selected.add(skill)
    unknown_forbidden = sorted(skill for skill in forbidden if skill not in registry)
    for skill in unknown_forbidden:
        planning_errors.append({"code": "unknown_forbidden_skill", "message": f"Unknown forbidden skill: {skill}"})

    conflicts = sorted(selected & forbidden)
    for skill in conflicts:
        planning_errors.append({"code": "forbidden_skill", "message": f"Required route conflicts with forbidden skill: {skill}"})
        _append_open_loop(planned, "POLICY", f"Route requires forbidden capability: {skill}")
    selected -= forbidden

    unknown_selected = sorted(skill for skill in selected if skill not in registry)
    for skill in unknown_selected:
        planning_errors.append({"code": "registry_gap", "message": f"Selected skill is absent from capability registry: {skill}"})
        selected.remove(skill)

    reused_artifacts: list[dict[str, str]] = []
    available_types = _validated_artifact_types(planned)
    reusable_skills = set()
    if constraints.get("reuse_validated_artifacts", True):
        for skill in selected:
            matching = sorted(SKILL_OUTPUT_TYPES.get(skill, set()) & available_types)
            if matching:
                reusable_skills.add(skill)
                reused_artifacts.append({"skill": skill, "artifact_type": matching[0]})
    selected -= reusable_skills

    dependency_map = {
        skill: {dependency for dependency in SKILL_DEPENDENCIES.get(skill, []) if dependency in selected}
        for skill in selected
    }
    archetype = str(planned.get("archetype", ""))
    if "failed-experiment" in archetype:
        if "zju-experiment-log" in selected and "zju-evidence-synthesis" in selected:
            dependency_map["zju-evidence-synthesis"].add("zju-experiment-log")
        if "zju-statistics-audit" in selected and "zju-hypothesis-design" in selected:
            dependency_map["zju-hypothesis-design"].add("zju-statistics-audit")
    if "retraction" in archetype or constraints.get("retraction_or_correction"):
        for upstream in ("zju-literature-monitor", "zju-reference-audit"):
            if upstream in selected and "zju-evidence-synthesis" in selected:
                dependency_map["zju-evidence-synthesis"].add(upstream)
    if constraints.get("unattended_loop_requested") and "zju-literature-monitor" in selected and "zju-evidence-synthesis" in selected:
        dependency_map["zju-evidence-synthesis"].add("zju-literature-monitor")
    if "zju-chemistry-databases" in selected:
        for downstream in ("zju-evidence-synthesis", "zju-hypothesis-design", "zju-paper-to-patent"):
            if downstream in selected:
                dependency_map[downstream].add("zju-chemistry-databases")
    if "zju-reference-audit" in selected and not ("retraction" in archetype or constraints.get("retraction_or_correction")):
        for upstream in ("zju-scientific-writing", "zju-proposal-writer", "zju-review-response"):
            if upstream in selected:
                dependency_map["zju-reference-audit"].add(upstream)
    order = _topological_order(selected, dependency_map)
    step_id_by_skill = {skill: f"S{index:02d}" for index, skill in enumerate(order, 1)}
    ceiling = constraints.get("autonomy_ceiling", "L2")
    try:
        ceiling_rank = autonomy_rank(ceiling)
    except ValueError:
        ceiling_rank = 0
        planning_errors.append({"code": "autonomy", "message": f"Unknown autonomy ceiling: {ceiling}"})
    requested_mode = planned.get("mode", "execute")
    target_rank = 1 if requested_mode in {"plan", "audit"} else 2
    step_level = f"L{min(target_rank, ceiling_rank)}"
    notes = constraints.get("notes", []) if isinstance(constraints.get("notes", []), list) else []
    bounded_l4 = (
        ceiling == "L4"
        and constraints.get("unattended_loop_requested") is True
        and len(notes) >= 2
        and "zju-reviewer" in selected
        and any("停止" in str(note) or "stop" in str(note).lower() for note in notes)
    )
    if constraints.get("unattended_loop_requested") and not bounded_l4:
        planning_errors.append({"code": "l4_controls", "message": "Unattended execution requires explicit budgets, stop rules, checkpoint preservation, and independent review"})

    context_gates: list[str] = []
    if constraints.get("ethics_required") or constraints.get("human_or_animal_subjects"):
        context_gates.append("ethics")
    if constraints.get("sensitive_data") or constraints.get("personal_data"):
        context_gates.append("privacy")
    release_intent = bool(constraints.get("release_intent")) or "submission_package" in deliverable_types
    mission_required_gates = planned.get("required_gates", [])
    if not isinstance(mission_required_gates, list):
        mission_required_gates = []
        planning_errors.append({"code": "required_gates_type", "message": "required_gates must be a list"})
    planned["mission_required_gates"] = list(dict.fromkeys(_normalize_gate(str(gate)) for gate in mission_required_gates))

    route: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    checkpoints: list[dict[str, str]] = []
    for skill in order:
        entry = registry[skill]
        dependencies = [
            step_id_by_skill[dependency]
            for dependency in dependency_map.get(skill, set())
            if dependency in step_id_by_skill
        ]
        dependencies.sort()
        for dependency in dependencies:
            edges.append({"from": dependency, "to": step_id_by_skill[skill]})
        gates = list(SKILL_GATES.get(skill, []))
        for registered_gate in entry.get("required_human_gates", []):
            if not isinstance(registered_gate, str):
                continue
            normalized_gate = _normalize_gate(registered_gate)
            triggered = (
                normalized_gate in {"patent_legal", "integrity"}
                or (normalized_gate == "external_action" and bool(constraints.get("credentialed_access") or constraints.get("external_mutation")))
                or (normalized_gate == "submission_release" and release_intent)
                or (normalized_gate == "privacy" and bool(constraints.get("sensitive_data") or constraints.get("personal_data")))
                or (normalized_gate == "ethics" and bool(constraints.get("ethics_required") or constraints.get("human_or_animal_subjects")))
            )
            if triggered:
                gates.append(normalized_gate)
        gates.extend(context_gates)
        external_state = entry.get("external_state", False)
        if external_state not in (False, None, "none", "read_only") and constraints.get("external_mutation"):
            gates.append("external_action")
        if release_intent and skill in {"zju-paper2ppt", "zju-data-availability", "zju-paper-to-patent", "zju-research-integrity"}:
            gates.append("submission_release")
        gates = list(dict.fromkeys(gates))
        validators = entry.get("validators", [])
        if not validators:
            validators = [{"type": "manual", "path": None, "note": "Use the specialist output contract and gate ledger"}]
        skill_autonomy = step_level
        if bounded_l4 and skill in {"zju-literature-search", "zju-literature-monitor", "zju-evidence-synthesis"}:
            skill_autonomy = "L4"
        elif constraints.get("credentialed_access") and skill == "zju-fulltext-access" and ceiling_rank >= 3:
            skill_autonomy = "L3"
            gates.append("external_action")
            gates = list(dict.fromkeys(gates))
        step = {
            "step_id": step_id_by_skill[skill],
            "stage": SKILL_STAGE.get(skill, entry.get("stages", ["integrity"])[0]),
            "skill": skill,
            "objective_ids": [item["objective_id"] for item in planned["objectives"] if isinstance(item, dict) and item.get("objective_id")],
            "accepted_inputs": entry.get("accepts", []),
            "expected_outputs": entry.get("produces", []),
            "prerequisites": dependencies,
            "required_gates": gates,
            "autonomy_level": skill_autonomy,
            "risk_level": entry.get("risk_level", "medium"),
            "validators": validators,
            "status": "ready" if not dependencies else "planned",
            "produced_artifact_ids": [],
            "stop_conditions": ["validator_failure", "critical_risk", "human_gate", "autonomy_or_budget_ceiling", "non_progress"],
        }
        if skill_autonomy == "L4":
            step["bounded_loop_controls"] = {
                "budget_constraints": copy.deepcopy(notes),
                "stop_on_failure": True,
                "checkpoint_state": True,
                "independent_review_skill": "zju-reviewer",
                "forbidden_actions": copy.deepcopy(constraints.get("forbidden_actions", [])),
            }
        route.append(step)
        for gate in gates:
            if gate in {"ethics", "privacy", "external_action", "patent_legal", "submission_release"}:
                checkpoint = {"step_id": step["step_id"], "gate": gate, "authority": "authorized_human"}
                if checkpoint not in checkpoints:
                    checkpoints.append(checkpoint)

    if route:
        planned["current_stage"] = route[0]["stage"]
    planned["route"] = route
    planned["dag"] = {"nodes": [step["step_id"] for step in route], "edges": edges}
    planned["execution_order"] = [step["step_id"] for step in route]
    planned["human_checkpoints"] = checkpoints
    for gate in planned["mission_required_gates"]:
        if gate in {"ethics", "privacy", "external_action", "patent_legal", "submission_release"}:
            checkpoint = {"step_id": None, "gate": gate, "authority": "authorized_human"}
            if checkpoint not in planned["human_checkpoints"]:
                planned["human_checkpoints"].append(checkpoint)
    planned["reused_validated_artifacts"] = reused_artifacts
    planned["planner_version"] = PLANNER_VERSION
    planned.setdefault("revision", 1)
    planned["planning_errors"] = planning_errors
    planned["unresolved_deliverables"] = unresolved
    # Framing and final state communication are Director-owned stages even when no
    # separate writing specialist is needed for the requested artifact.
    covered_stages = ["framing"] + sorted(
        {"communication", *(step["stage"] for step in route)}, key=lambda stage: STAGE_ORDER[stage]
    )
    planned["stage_plan"] = covered_stages
    ready_steps = [step for step in route if step["status"] == "ready"]
    planned["next_action"] = (
        {"step_id": ready_steps[0]["step_id"], "skill": ready_steps[0]["skill"], "action": "execute_ready_step"}
        if ready_steps
        else {"action": "resolve_blocking_open_loop" if planning_errors or unresolved else "evaluate_release_gates"}
    )
    planned["status"] = "blocked" if planning_errors or unresolved else "planned"
    hash_input = copy.deepcopy(planned)
    hash_input.pop("plan_hash", None)
    hash_input.pop("planning_validation", None)
    planned["plan_hash"] = stable_hash(hash_input)
    planned["planning_validation"] = validate(planned)
    if not planned["planning_validation"]["valid"]:
        planned["status"] = "blocked"
    return planned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args()
    planned = plan_mission(load_document(args.input), args.registry)
    write_document(args.output, planned)
    return 0 if planned["planning_validation"]["valid"] and not planned["planning_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
\n