#!/usr/bin/env python3
"""Audit proposal schedule, dependency, resource, milestone, and risk feasibility."""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any


ORACLE_ID = "proposal_schedule_resource_risk_v1"
LEVELS = {"low": 1, "medium": 2, "high": 3}


def audit(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    project_weeks = payload.get("project_weeks")
    if not isinstance(project_weeks, int) or isinstance(project_weeks, bool) or project_weeks <= 0:
        findings.append({"field": "project_weeks", "message": "project_weeks must be a positive integer"})
        project_weeks = 0
    capacities = payload.get("resource_capacities")
    if not isinstance(capacities, dict) or not capacities or any(not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 for value in capacities.values()):
        findings.append({"field": "resource_capacities", "message": "non-negative numeric capacities are required"})
        capacities = {}
    work_rows = payload.get("work_packages")
    if not isinstance(work_rows, list) or not work_rows:
        findings.append({"field": "work_packages", "message": "at least one work package is required"})
        work_rows = []
    work: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(work_rows):
        prefix = f"work_packages[{index}]"
        if not isinstance(row, dict):
            findings.append({"field": prefix, "message": "work package must be an object"})
            continue
        wp_id = str(row.get("wp_id") or "")
        if not wp_id or wp_id in work:
            findings.append({"field": f"{prefix}.wp_id", "message": "wp_id must be unique and non-empty"})
            continue
        start, end = row.get("start_week"), row.get("end_week")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (start, end)) or not (1 <= start <= end <= project_weeks):
            findings.append({"field": prefix, "message": "work package weeks must satisfy 1 <= start <= end <= project_weeks"})
        if not str(row.get("owner") or "").strip():
            findings.append({"field": f"{prefix}.owner", "message": "accountable owner is required"})
        demand = row.get("resource_demand")
        if not isinstance(demand, dict):
            findings.append({"field": f"{prefix}.resource_demand", "message": "resource_demand must be an object"})
        else:
            for resource, amount in demand.items():
                if resource not in capacities:
                    findings.append({"field": f"{prefix}.resource_demand", "message": f"unknown resource: {resource}"})
                if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
                    findings.append({"field": f"{prefix}.resource_demand.{resource}", "message": "demand must be non-negative numeric"})
        work[wp_id] = row

    indegree = {wp_id: 0 for wp_id in work}
    downstream = {wp_id: set() for wp_id in work}
    for wp_id, row in work.items():
        dependencies = row.get("dependencies", [])
        if not isinstance(dependencies, list):
            findings.append({"field": f"work_packages[{wp_id}].dependencies", "message": "dependencies must be a list"})
            dependencies = []
        for parent in {str(item) for item in dependencies}:
            if parent not in work:
                findings.append({"field": f"work_packages[{wp_id}].dependencies", "message": f"unknown dependency: {parent}"})
                continue
            if isinstance(work[parent].get("end_week"), int) and isinstance(row.get("start_week"), int) and work[parent]["end_week"] >= row["start_week"]:
                findings.append({"field": f"work_packages[{wp_id}].start_week", "message": f"starts before dependency {parent} has finished"})
            downstream[parent].add(wp_id)
            indegree[wp_id] += 1
    queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in sorted(downstream[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    cycle = sorted(set(work) - set(order))
    if cycle:
        findings.append({"field": "work_packages.dependencies", "message": f"dependency cycle: {cycle}"})

    overloads: list[dict[str, Any]] = []
    for week in range(1, project_weeks + 1):
        use = {resource: 0.0 for resource in capacities}
        for row in work.values():
            if isinstance(row.get("start_week"), int) and isinstance(row.get("end_week"), int) and row["start_week"] <= week <= row["end_week"]:
                for resource, amount in (row.get("resource_demand") or {}).items():
                    if resource in use and isinstance(amount, (int, float)) and not isinstance(amount, bool):
                        use[resource] += float(amount)
        for resource, amount in use.items():
            if amount > float(capacities[resource]) + 1e-12:
                overloads.append({"week": week, "resource": resource, "demand": amount, "capacity": capacities[resource]})
                findings.append({"field": "resource_capacities", "message": f"{resource} overloaded in week {week}: {amount} > {capacities[resource]}"})

    milestones = payload.get("milestones")
    milestones = milestones if isinstance(milestones, list) else []
    milestone_wps: set[str] = set()
    for index, row in enumerate(milestones):
        prefix = f"milestones[{index}]"
        if not isinstance(row, dict) or str(row.get("wp_id") or "") not in work:
            findings.append({"field": prefix, "message": "milestone must reference a known work package"})
            continue
        wp_id = str(row["wp_id"])
        milestone_wps.add(wp_id)
        due = row.get("due_week")
        if not isinstance(due, int) or isinstance(due, bool) or due < work[wp_id].get("end_week", project_weeks + 1) or due > project_weeks:
            findings.append({"field": f"{prefix}.due_week", "message": "milestone must occur at or after its work package end and within the project"})
        if not str(row.get("decision_rule") or "").strip():
            findings.append({"field": f"{prefix}.decision_rule", "message": "testable decision rule is required"})
    for wp_id, row in work.items():
        if row.get("critical") is True and wp_id not in milestone_wps:
            findings.append({"field": f"work_packages[{wp_id}]", "message": "critical work package lacks a decision milestone"})

    risks = payload.get("risks")
    risks = risks if isinstance(risks, list) else []
    risk_coverage: set[str] = set()
    for index, row in enumerate(risks):
        prefix = f"risks[{index}]"
        if not isinstance(row, dict):
            findings.append({"field": prefix, "message": "risk must be an object"})
            continue
        probability, impact = row.get("probability"), row.get("impact")
        if probability not in LEVELS or impact not in LEVELS:
            findings.append({"field": prefix, "message": "probability and impact must be low, medium, or high"})
        linked = row.get("wp_ids")
        if not isinstance(linked, list) or not linked:
            findings.append({"field": f"{prefix}.wp_ids", "message": "risk must link to at least one work package"})
            linked = []
        for wp_id in {str(item) for item in linked}:
            if wp_id not in work:
                findings.append({"field": f"{prefix}.wp_ids", "message": f"unknown work package: {wp_id}"})
            else:
                risk_coverage.add(wp_id)
        for field in ("trigger", "mitigation", "contingency", "owner"):
            if not str(row.get(field) or "").strip():
                findings.append({"field": f"{prefix}.{field}", "message": "required risk control missing"})
    for wp_id, row in work.items():
        if row.get("critical") is True and wp_id not in risk_coverage:
            findings.append({"field": f"work_packages[{wp_id}]", "message": "critical work package lacks a linked risk and contingency"})

    return {"oracle_id": ORACLE_ID, "valid": bool(work) and not findings, "topological_order": order, "dependency_cycle": cycle, "resource_overloads": overloads, "risk_coverage": sorted(risk_coverage), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = audit(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
