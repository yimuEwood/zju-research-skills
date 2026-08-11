#!/usr/bin/env python3
"""Compile and validate an evidence-to-execution proposal manifest."""

from __future__ import annotations

import argparse
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any


CAPABILITY_STATUS = {"available", "partial", "missing", "unverified"}
CAPABILITY_DIMENSIONS = {"scientific", "data_sample", "method", "instrument", "personnel", "time", "budget", "ethics", "collaboration"}
BRANCHES = ("success", "inconclusive", "failure")


def add_finding(findings: list[dict[str, str]], field: str, message: str, severity: str = "error") -> None:
    findings.append({"severity": severity, "field": field, "message": message})


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def string_list(value: Any) -> list[str]:
    return [str(item) for item in as_list(value) if item not in (None, "")]


def index_rows(rows: Any, id_field: str, field: str, findings: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(as_list(rows), 1):
        if not isinstance(row, dict):
            add_finding(findings, f"{field}[{position}]", "row must be an object")
            continue
        row_id = str(row.get(id_field) or "")
        if not row_id:
            add_finding(findings, f"{field}[{position}].{id_field}", "required field missing")
        elif row_id in indexed:
            add_finding(findings, f"{field}[{position}].{id_field}", f"duplicate ID: {row_id}")
        else:
            indexed[row_id] = row
    return indexed


def require_fields(row: dict[str, Any], fields: tuple[str, ...], prefix: str, findings: list[dict[str, str]]) -> None:
    for field in fields:
        if row.get(field) in (None, "", []):
            add_finding(findings, f"{prefix}.{field}", "required field missing")


def unknown_links(
    values: Any,
    known: set[str],
    field: str,
    findings: list[dict[str, str]],
) -> set[str]:
    links = set(string_list(values))
    unknown = sorted(links - known)
    if unknown:
        add_finding(findings, field, f"unknown IDs: {unknown}")
    return links


def topological_order(work_packages: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    dependencies = {
        wp_id: set(string_list(row.get("dependencies")))
        for wp_id, row in work_packages.items()
    }
    indegree = {wp_id: 0 for wp_id in work_packages}
    downstream = {wp_id: set() for wp_id in work_packages}
    for wp_id, parents in dependencies.items():
        for parent in parents & set(work_packages):
            downstream[parent].add(wp_id)
            indegree[wp_id] += 1
    ready = deque(sorted(wp_id for wp_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for child in sorted(downstream[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    cycle = sorted(set(work_packages) - set(order))
    return order, cycle


def derived_views(payload: dict[str, Any]) -> dict[str, Any]:
    objectives = {
        str(row.get("objective_id")): row
        for row in as_list(payload.get("objectives"))
        if isinstance(row, dict) and row.get("objective_id")
    }
    work_packages = {
        str(row.get("work_package_id")): row
        for row in as_list(payload.get("work_packages"))
        if isinstance(row, dict) and row.get("work_package_id")
    }
    capabilities = {
        str(row.get("capability_id")): row
        for row in as_list(payload.get("capabilities"))
        if isinstance(row, dict) and row.get("capability_id")
    }
    sequence, cycle = topological_order(work_packages)
    objective_map = [
        {
            "objective_id": objective_id,
            "hypothesis_ids": string_list(objective.get("hypothesis_ids")),
            "work_package_ids": sorted(
                wp_id for wp_id, wp in work_packages.items()
                if objective_id in string_list(wp.get("objective_ids"))
            ),
        }
        for objective_id, objective in sorted(objectives.items())
    ]
    milestone_tree: list[dict[str, Any]] = []
    for wp_id in sequence + cycle:
        wp = work_packages[wp_id]
        gate = wp.get("decision_gate") if isinstance(wp.get("decision_gate"), dict) else {}
        for milestone in as_list(wp.get("milestones")):
            if not isinstance(milestone, dict):
                continue
            milestone_tree.append({
                "milestone_id": milestone.get("milestone_id"),
                "work_package_id": wp_id,
                "month": milestone.get("month"),
                "deliverable": milestone.get("deliverable"),
                "acceptance_criteria": milestone.get("acceptance_criteria"),
                "decision_metric": gate.get("metric"),
                "branches": gate.get("branches", {}),
            })
    feasibility: list[dict[str, Any]] = []
    for wp_id in sequence + cycle:
        for capability_id in string_list(work_packages[wp_id].get("capability_ids")):
            capability = capabilities.get(capability_id, {})
            feasibility.append({
                "work_package_id": wp_id,
                "capability_id": capability_id,
                "item": capability.get("item"),
                "dimension": capability.get("dimension"),
                "status": capability.get("status", "unverified"),
                "evidence_ids": string_list(capability.get("evidence_ids")),
                "constraint": capability.get("constraint", ""),
                "mitigation": capability.get("mitigation", ""),
            })
    status_counts = Counter(str(row["status"]) for row in feasibility)
    return {
        "objective_work_package_map": objective_map,
        "work_package_sequence": sequence,
        "dependency_cycle": cycle,
        "milestone_decision_tree": milestone_tree,
        "feasibility_matrix": feasibility,
        "feasibility_summary": dict(sorted(status_counts.items())),
    }


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for field in ("proposal_id", "mode", "scheme_status", "objectives", "work_packages", "evidence", "risks", "compliance"):
        if payload.get(field) in (None, "", []):
            add_finding(findings, field, "required field missing")

    v2 = str(payload.get("workflow_version") or "") == "2.0"
    evidence = index_rows(payload.get("evidence"), "evidence_id", "evidence", findings)
    objectives = index_rows(payload.get("objectives"), "objective_id", "objectives", findings)
    work_packages = index_rows(payload.get("work_packages"), "work_package_id", "work_packages", findings)
    hypotheses = index_rows(payload.get("hypotheses"), "hypothesis_id", "hypotheses", findings) if v2 else {}
    capabilities = index_rows(payload.get("capabilities"), "capability_id", "capabilities", findings) if v2 else {}

    if v2 and not hypotheses:
        add_finding(findings, "hypotheses", "workflow 2.0 requires competing-hypothesis records")
    if v2 and not capabilities:
        add_finding(findings, "capabilities", "workflow 2.0 requires an evidence-backed capability catalog")

    if v2:
        for evidence_id, item in evidence.items():
            require_fields(item, ("status", "source_anchor"), f"evidence[{evidence_id}]", findings)

    for hypothesis_id, item in hypotheses.items():
        prefix = f"hypotheses[{hypothesis_id}]"
        require_fields(item, ("statement", "evidence_ids", "predictions", "falsifiers"), prefix, findings)
        unknown_links(item.get("evidence_ids"), set(evidence), f"{prefix}.evidence_ids", findings)

    for objective_id, item in objectives.items():
        prefix = f"objectives[{objective_id}]"
        require_fields(item, ("question", "success_criteria", "evidence_ids"), prefix, findings)
        unknown_links(item.get("evidence_ids"), set(evidence), f"{prefix}.evidence_ids", findings)
        if v2:
            require_fields(item, ("hypothesis_ids",), prefix, findings)
            linked_hypotheses = unknown_links(item.get("hypothesis_ids"), set(hypotheses), f"{prefix}.hypothesis_ids", findings)
            if len(linked_hypotheses) < 2:
                add_finding(findings, f"{prefix}.hypothesis_ids", "objective has no explicit competing hypothesis", severity="warning")

    covered_objectives: set[str] = set()
    covered_hypotheses: set[str] = set()
    for wp_id, item in work_packages.items():
        prefix = f"work_packages[{wp_id}]"
        require_fields(item, ("objective_ids", "methods", "outputs", "milestones", "decision_gate"), prefix, findings)
        covered_objectives |= unknown_links(item.get("objective_ids"), set(objectives), f"{prefix}.objective_ids", findings)
        if not v2:
            continue
        require_fields(
            item,
            ("hypothesis_ids", "experiment_ids", "evidence_ids", "inputs", "experimental_unit", "capability_ids", "owner", "timeline"),
            prefix,
            findings,
        )
        covered_hypotheses |= unknown_links(item.get("hypothesis_ids"), set(hypotheses), f"{prefix}.hypothesis_ids", findings)
        unknown_links(item.get("evidence_ids"), set(evidence), f"{prefix}.evidence_ids", findings)
        unknown_links(item.get("capability_ids"), set(capabilities), f"{prefix}.capability_ids", findings)
        if not isinstance(item.get("dependencies"), list):
            add_finding(findings, f"{prefix}.dependencies", "dependencies must be a list; use [] for the first work package")
        dependencies = unknown_links(item.get("dependencies"), set(work_packages), f"{prefix}.dependencies", findings)
        if wp_id in dependencies:
            add_finding(findings, f"{prefix}.dependencies", "work package cannot depend on itself")
        timeline = item.get("timeline") if isinstance(item.get("timeline"), dict) else {}
        if not isinstance(timeline.get("start_month"), int) or not isinstance(timeline.get("end_month"), int):
            add_finding(findings, f"{prefix}.timeline", "start_month and end_month must be integers")
        elif timeline["start_month"] < 1 or timeline["end_month"] < timeline["start_month"]:
            add_finding(findings, f"{prefix}.timeline", "invalid project-month interval")
        gate = item.get("decision_gate") if isinstance(item.get("decision_gate"), dict) else {}
        if not gate.get("metric"):
            add_finding(findings, f"{prefix}.decision_gate.metric", "decision metric missing")
        branches = gate.get("branches") if isinstance(gate.get("branches"), dict) else {}
        for branch_name in BRANCHES:
            branch = branches.get(branch_name)
            if not isinstance(branch, dict):
                add_finding(findings, f"{prefix}.decision_gate.branches.{branch_name}", "branch must contain condition and next action")
            else:
                require_fields(branch, ("condition", "next"), f"{prefix}.decision_gate.branches.{branch_name}", findings)
        milestones = as_list(item.get("milestones"))
        if not milestones or any(not isinstance(row, dict) for row in milestones):
            add_finding(findings, f"{prefix}.milestones", "workflow 2.0 milestones must be structured rows")
        else:
            for position, milestone in enumerate(milestones, 1):
                require_fields(
                    milestone,
                    ("milestone_id", "month", "deliverable", "acceptance_criteria"),
                    f"{prefix}.milestones[{position}]",
                    findings,
                )

    missing_objectives = set(objectives) - covered_objectives
    if missing_objectives:
        add_finding(findings, "work_packages", f"objectives without work packages: {sorted(missing_objectives)}")
    if v2:
        missing_hypotheses = set(hypotheses) - covered_hypotheses
        if missing_hypotheses:
            add_finding(findings, "work_packages", f"hypotheses without discriminating work packages: {sorted(missing_hypotheses)}")
        for capability_id, item in capabilities.items():
            prefix = f"capabilities[{capability_id}]"
            require_fields(item, ("item", "dimension", "status", "evidence_ids"), prefix, findings)
            if item.get("dimension") not in CAPABILITY_DIMENSIONS:
                add_finding(findings, f"{prefix}.dimension", "invalid feasibility dimension")
            if item.get("status") not in CAPABILITY_STATUS:
                add_finding(findings, f"{prefix}.status", "invalid capability status")
            unknown_links(item.get("evidence_ids"), set(evidence), f"{prefix}.evidence_ids", findings)
            if item.get("status") != "available" and (not item.get("constraint") or not item.get("mitigation")):
                add_finding(findings, prefix, "non-available capability requires constraint and mitigation")
        _, cycle = topological_order(work_packages)
        if cycle:
            add_finding(findings, "work_packages.dependencies", f"dependency cycle: {cycle}")

    submission_ready = bool(payload.get("submission_ready", False))
    if submission_ready and payload.get("scheme_status") != "official_verified":
        add_finding(findings, "scheme_status", "submission-ready package needs verified official scheme")

    derived = derived_views(payload)
    errors = [item for item in findings if item["severity"] == "error"]
    valid = not errors
    start_blocked = any(row.get("status") in {"missing", "unverified"} for row in derived["feasibility_matrix"])
    return {
        "valid": valid,
        "execution_plan_ready": bool(v2 and valid),
        "ready_to_start": bool(v2 and valid and not start_blocked),
        "submission_ready": bool(submission_ready and valid),
        **derived,
        "findings": findings,
    }


def hypothesis_plan_adapter(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt a zju-hypothesis-design payload without discarding experiment semantics."""
    nested = payload.get("hypothesis_plan")
    plan = nested if isinstance(nested, dict) else payload
    adapted = dict(payload)
    hypotheses_in = as_list(plan.get("hypotheses"))
    experiments_in = as_list(plan.get("discrimination_experiments")) or as_list(plan.get("experiments"))

    hypotheses: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    hypothesis_evidence: dict[str, list[str]] = {}
    for row in hypotheses_in:
        if not isinstance(row, dict):
            continue
        normalized = dict(row)
        normalized.setdefault("statement", row.get("mechanism", ""))
        normalized.setdefault("predictions", row.get("unique_predictions", []))
        linked_evidence = string_list(row.get("evidence_ids"))
        hypothesis_evidence[str(row.get("hypothesis_id") or "")] = linked_evidence
        evidence_ids.update(linked_evidence)
        hypotheses.append(normalized)

    schedule = payload.get("schedule_by_experiment") if isinstance(payload.get("schedule_by_experiment"), dict) else {}
    method_map = payload.get("method_by_experiment") if isinstance(payload.get("method_by_experiment"), dict) else {}
    explicit_objectives = as_list(payload.get("objectives")) or as_list(plan.get("objectives"))
    default_objective_id = str(explicit_objectives[0].get("objective_id")) if len(explicit_objectives) == 1 and isinstance(explicit_objectives[0], dict) else ""
    adapted_experiments: list[dict[str, Any]] = []
    generated_objectives: list[dict[str, Any]] = []

    for position, row in enumerate(experiments_in, 1):
        if not isinstance(row, dict):
            continue
        experiment = dict(row)
        experiment_id = str(row.get("experiment_id") or f"MISSING-{position}")
        linked_hypotheses = string_list(row.get("hypothesis_ids"))
        linked_evidence = set(string_list(row.get("evidence_ids")))
        for hypothesis_id in linked_hypotheses:
            linked_evidence.update(hypothesis_evidence.get(hypothesis_id, []))
        evidence_ids.update(linked_evidence)

        objective_ids = string_list(row.get("objective_ids"))
        if not objective_ids:
            objective_ids = [default_objective_id or f"O-{experiment_id}"]
        if not explicit_objectives:
            decision_text = row.get("decision_rule")
            if isinstance(decision_text, dict):
                decision_text = json.dumps(decision_text, ensure_ascii=False, sort_keys=True)
            primary_outcome = str(row.get("primary_outcome") or "specified primary outcome")
            question = row.get("question") or payload.get("research_question") or plan.get("research_question")
            question = question or f"Discriminate {', '.join(linked_hypotheses)} using {primary_outcome}"
            generated_objectives.append({
                "objective_id": objective_ids[0],
                "question": question,
                "hypothesis_ids": linked_hypotheses,
                "evidence_ids": sorted(linked_evidence),
                "success_criteria": [str(decision_text or "Apply the prospective decision rule")],
            })

        timing = schedule.get(experiment_id) if isinstance(schedule.get(experiment_id), dict) else {}
        experiment.setdefault("objective_ids", objective_ids)
        experiment.setdefault("evidence_ids", sorted(linked_evidence))
        experiment.setdefault("inputs", [
            f"intervention: {row.get('intervention')}",
            f"control: {row.get('control')}",
        ])
        experiment.setdefault("methods", string_list(method_map.get(experiment_id)) or [
            f"Execute intervention/control comparison specified by hypothesis experiment {experiment_id}"
        ])
        experiment.setdefault("outputs", [str(row.get("primary_outcome") or "")])
        experiment.setdefault("start_month", timing.get("start_month"))
        experiment.setdefault("end_month", timing.get("end_month"))
        experiment.setdefault("owner", payload.get("default_owner", "unassigned"))
        experiment.setdefault("milestone_acceptance", str(row.get("decision_rule") or ""))
        experiment["source_experiment"] = dict(row)
        adapted_experiments.append(experiment)

    explicit_evidence = as_list(payload.get("evidence")) or as_list(plan.get("evidence"))
    evidence_by_id = {
        str(row.get("evidence_id")): dict(row)
        for row in explicit_evidence
        if isinstance(row, dict) and row.get("evidence_id")
    }
    for evidence_id in sorted(evidence_ids):
        evidence_by_id.setdefault(evidence_id, {
            "evidence_id": evidence_id,
            "status": "upstream_id_only",
            "source_anchor": f"upstream-evidence-id:{evidence_id}",
        })

    adapted["hypotheses"] = hypotheses
    adapted["experiments"] = adapted_experiments
    adapted["objectives"] = explicit_objectives or generated_objectives
    adapted["evidence"] = list(evidence_by_id.values())
    adapted.setdefault("source_handoff", {
        "type": "zju-hypothesis-design",
        "schema_version": plan.get("schema_version", "legacy"),
    })
    return adapted


def compile_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    payload = hypothesis_plan_adapter(payload)
    experiments = as_list(payload.get("experiments"))
    experiment_to_wp: dict[str, str] = {}
    for position, experiment in enumerate(experiments, 1):
        if not isinstance(experiment, dict):
            continue
        experiment_id = str(experiment.get("experiment_id") or f"MISSING-{position}")
        experiment_to_wp[experiment_id] = str(experiment.get("work_package_id") or f"WP-{experiment_id}")

    work_packages: list[dict[str, Any]] = []
    for position, experiment in enumerate(experiments, 1):
        if not isinstance(experiment, dict):
            continue
        experiment_id = str(experiment.get("experiment_id") or f"MISSING-{position}")
        wp_id = experiment_to_wp[experiment_id]
        outputs = string_list(experiment.get("outputs"))
        decision_rule = experiment.get("decision_rule")
        if not isinstance(decision_rule, dict):
            original_rule = str(experiment.get("decision_rule") or "")
            predicted = experiment.get("predicted_outcomes")
            predicted_text = json.dumps(predicted, ensure_ascii=False, sort_keys=True) if isinstance(predicted, dict) else ""
            decision_rule = {
                "metric": experiment.get("decision_metric") or experiment.get("primary_outcome", ""),
                "branches": {
                    "success": {
                        "condition": experiment.get("success_rule") or original_rule,
                        "next": experiment.get("next_if_success", "advance"),
                    },
                    "inconclusive": {
                        "condition": experiment.get("inconclusive_rule") or experiment.get("inconclusive_region", ""),
                        "next": experiment.get("next_if_inconclusive", "repeat_or_redesign"),
                    },
                    "failure": {
                        "condition": experiment.get("failure_rule") or (f"Observed pattern does not satisfy the prospective rule; compare {predicted_text}" if predicted_text else ""),
                        "next": experiment.get("next_if_failure", "pivot_or_stop"),
                    },
                },
            }
        work_packages.append({
            "work_package_id": wp_id,
            "objective_ids": string_list(experiment.get("objective_ids")),
            "hypothesis_ids": string_list(experiment.get("hypothesis_ids")),
            "experiment_ids": [experiment_id],
            "evidence_ids": string_list(experiment.get("evidence_ids")),
            "inputs": string_list(experiment.get("inputs")),
            "experimental_unit": experiment.get("experimental_unit", ""),
            "intervention": experiment.get("intervention"),
            "control": experiment.get("control"),
            "primary_outcome": experiment.get("primary_outcome"),
            "predicted_outcomes": experiment.get("predicted_outcomes"),
            "inconclusive_region": experiment.get("inconclusive_region"),
            "prospective_decision_rule": experiment.get("decision_rule"),
            "source_experiment": experiment.get("source_experiment", dict(experiment)),
            "methods": string_list(experiment.get("methods")) or ([str(experiment["method"])] if experiment.get("method") else []),
            "outputs": outputs,
            "capability_ids": string_list(experiment.get("capability_ids")),
            "dependencies": [
                experiment_to_wp.get(dependency, f"WP-{dependency}")
                for dependency in string_list(experiment.get("depends_on_experiment_ids"))
            ],
            "owner": experiment.get("owner", ""),
            "timeline": {
                "start_month": experiment.get("start_month"),
                "end_month": experiment.get("end_month"),
            },
            "milestones": [{
                "milestone_id": experiment.get("milestone_id") or f"M-{wp_id}",
                "month": experiment.get("end_month"),
                "deliverable": experiment.get("milestone_deliverable") or ("; ".join(outputs) if outputs else ""),
                "acceptance_criteria": experiment.get("milestone_acceptance", ""),
            }],
            "decision_gate": decision_rule,
        })

    manifest = {
        "workflow_version": "2.0",
        "proposal_id": payload.get("proposal_id"),
        "mode": payload.get("mode", "compose"),
        "scheme_status": payload.get("scheme_status", "template_pending"),
        "objectives": as_list(payload.get("objectives")),
        "hypotheses": as_list(payload.get("hypotheses")),
        "work_packages": work_packages,
        "capabilities": as_list(payload.get("capabilities")),
        "evidence": as_list(payload.get("evidence")),
        "risks": as_list(payload.get("risks")),
        "compliance": as_list(payload.get("compliance")),
        "submission_ready": bool(payload.get("submission_ready", False)),
    }
    report = validate(manifest)
    return {"manifest": manifest, **report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--compile-handoff", action="store_true", help="Compile evidence/hypothesis handoff into a proposal manifest before validation")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = compile_handoff(payload) if args.compile_handoff else validate(payload)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
