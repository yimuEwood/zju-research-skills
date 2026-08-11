#!/usr/bin/env python3
"""Run deterministic route and safety checks against the Director mission suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "zju-research-director" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import autonomy_rank  # noqa: E402
from plan_mission import plan_mission  # noqa: E402


TRIGGER_GATES = {
    "credentialed_access": "external_action",
    "human_participants": "ethics",
    "sensitive_data": "privacy",
    "patent_output": "patent_legal",
    "submission": "submission_release",
}


def _ordered(index: dict[str, int], before: str, after: str) -> bool:
    return before not in index or after not in index or index[before] < index[after]


def _case_checks(case: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not plan.get("planning_validation", {}).get("valid"):
        failures.append("planned mission does not validate")
    if plan.get("planning_errors"):
        failures.append("planning_errors is not empty")
    if plan.get("unresolved_deliverables"):
        failures.append("custom deliverables were left unrouted")

    route = plan.get("route", [])
    actual_skills = [step.get("skill") for step in route]
    actual_set = set(actual_skills)
    required = set(case.get("required_skills", []))
    forbidden = set(case.get("forbidden_skills", []))
    if not required.issubset(actual_set):
        failures.append("missing required skills: " + ", ".join(sorted(required - actual_set)))
    if actual_set & forbidden:
        failures.append("forbidden skills selected: " + ", ".join(sorted(actual_set & forbidden)))

    expected_stages = set(case.get("expected_stages", []))
    actual_stages = set(plan.get("stage_plan", []))
    if not expected_stages.issubset(actual_stages):
        failures.append("missing expected stages: " + ", ".join(sorted(expected_stages - actual_stages)))

    expected_gates = set(case.get("required_gates", []))
    actual_gates = set(plan.get("mission_required_gates", []))
    if not expected_gates.issubset(actual_gates):
        failures.append("missing mission gates: " + ", ".join(sorted(expected_gates - actual_gates)))
    for trigger, gate in TRIGGER_GATES.items():
        if trigger in case.get("risk_triggers", []) and gate not in actual_gates:
            failures.append(f"risk trigger {trigger} lacks {gate} gate")

    ceiling = case.get("autonomy_ceiling", "L2")
    for step in route:
        try:
            if autonomy_rank(step.get("autonomy_level")) > autonomy_rank(ceiling):
                failures.append(f"{step.get('step_id')} exceeds autonomy ceiling")
        except ValueError:
            failures.append(f"{step.get('step_id')} has invalid autonomy level")

    step_ids = {step.get("step_id") for step in route}
    position_by_step = {step.get("step_id"): index for index, step in enumerate(route)}
    for step in route:
        for dependency in step.get("prerequisites", []):
            if dependency not in step_ids:
                failures.append(f"{step.get('step_id')} references unknown prerequisite {dependency}")
            elif position_by_step[dependency] >= position_by_step[step.get("step_id")]:
                failures.append(f"{step.get('step_id')} is ordered before prerequisite {dependency}")
    dag_edges = {(edge.get("from"), edge.get("to")) for edge in plan.get("dag", {}).get("edges", [])}
    route_edges = {
        (dependency, step.get("step_id"))
        for step in route
        for dependency in step.get("prerequisites", [])
    }
    if dag_edges != route_edges:
        failures.append("DAG edges do not match route prerequisites")

    index = {skill: offset for offset, skill in enumerate(actual_skills)}
    archetype = case.get("archetype", "")
    order_rules: list[tuple[str, str]] = []
    if archetype == "question-to-proposal":
        order_rules.extend([
            ("zju-evidence-synthesis", "zju-hypothesis-design"),
            ("zju-hypothesis-design", "zju-proposal-writer"),
            ("zju-proposal-writer", "zju-reference-audit"),
        ])
    if archetype == "raw-data-to-manuscript":
        order_rules.extend([
            ("zju-experiment-log", "zju-statistics-audit"),
            ("zju-statistics-audit", "zju-scientific-writing"),
            ("zju-scientific-writing", "zju-reviewer"),
        ])
    if archetype in {"paper-to-ppt", "paper-to-ppt-and-patent-triage"}:
        order_rules.extend([
            ("zju-fulltext-access", "zju-paper-reader"),
            ("zju-paper-reader", "zju-paper2ppt"),
            ("zju-paper-reader", "zju-paper-to-patent"),
        ])
    if archetype == "retraction-or-correction-update":
        order_rules.extend([
            ("zju-literature-monitor", "zju-evidence-synthesis"),
            ("zju-reference-audit", "zju-evidence-synthesis"),
            ("zju-evidence-synthesis", "zju-scientific-writing"),
        ])
    if archetype == "failed-experiment-redesign":
        order_rules.extend([
            ("zju-experiment-log", "zju-statistics-audit"),
            ("zju-statistics-audit", "zju-hypothesis-design"),
            ("zju-evidence-synthesis", "zju-hypothesis-design"),
        ])
    if archetype == "database-to-experiment-design":
        order_rules.append(("zju-chemistry-databases", "zju-hypothesis-design"))
    if archetype == "bounded-living-evidence-loop":
        order_rules.extend([
            ("zju-literature-search", "zju-literature-monitor"),
            ("zju-literature-monitor", "zju-evidence-synthesis"),
            ("zju-scientific-writing", "zju-reviewer"),
        ])
        l4_steps = [step for step in route if step.get("autonomy_level") == "L4"]
        if not l4_steps:
            failures.append("bounded L4 case has no L4 step")
        for step in l4_steps:
            controls = step.get("bounded_loop_controls", {})
            if not controls.get("budget_constraints") or not controls.get("stop_on_failure") or not controls.get("checkpoint_state"):
                failures.append(f"{step.get('step_id')} lacks complete bounded-loop controls")
            if controls.get("independent_review_skill") != "zju-reviewer":
                failures.append(f"{step.get('step_id')} lacks independent review")
    for before, after in order_rules:
        if not _ordered(index, before, after):
            failures.append(f"route order violates {before} -> {after}")

    if not plan.get("next_action"):
        failures.append("next_action is missing")
    return failures


def run(cases_path: Path) -> dict[str, Any]:
    suite = json.loads(cases_path.read_text(encoding="utf-8-sig"))
    reports: list[dict[str, Any]] = []
    for case in suite.get("cases", []):
        first = plan_mission(case)
        second = plan_mission(case)
        failures = _case_checks(case, first)
        if first.get("plan_hash") != second.get("plan_hash") or first.get("route") != second.get("route"):
            failures.append("planner output is not deterministic")
        reports.append(
            {
                "mission_id": case.get("mission_id"),
                "passed": not failures,
                "route_length": len(first.get("route", [])),
                "plan_hash": first.get("plan_hash"),
                "failures": failures,
            }
        )
    passed = sum(report["passed"] for report in reports)
    return {
        "valid": bool(reports) and passed == len(reports),
        "suite_id": suite.get("suite_id"),
        "cases": len(reports),
        "passed": passed,
        "failed": len(reports) - passed,
        "reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "director-cases.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.cases)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
