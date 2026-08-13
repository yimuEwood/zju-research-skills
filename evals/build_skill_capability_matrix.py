#!/usr/bin/env python3
"""Build an evidence inventory for every skill without turning coverage into a capability score."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
TESTS = ROOT / "tests"
FIXTURE_REGISTRY = ROOT / "evals/fixtures/portfolio-capability-fixtures.json"
CASE_SUITES = (
    ROOT / "evals/cases.json",
    ROOT / "evals/expansion-cases.json",
)
DIRECTOR_SUITE = ROOT / "evals/director-cases.json"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _case_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in CASE_SUITES:
        for case in _json(path)["cases"]:
            skill = str(case["skill"])
            counts[skill] = counts.get(skill, 0) + 1
    counts["zju-research-director"] = len(_json(DIRECTOR_SUITE)["cases"])
    return counts


def build() -> dict[str, Any]:
    fixture_rows = {row["skill"]: row for row in _json(FIXTURE_REGISTRY)["fixtures"]}
    case_counts = _case_counts()
    test_text = "\n".join(path.read_text(encoding="utf-8") for path in TESTS.glob("test_*.py"))
    rows: list[dict[str, Any]] = []
    for skill_dir in sorted(path for path in SKILLS.iterdir() if path.is_dir()):
        scripts = sorted(path.name for path in (skill_dir / "scripts").glob("*.py"))
        referenced_scripts = [name for name in scripts if name in test_text]
        directly_referenced = f"skills/{skill_dir.name}/scripts/" in test_text.replace("\\", "/")
        fixture = fixture_rows.get(skill_dir.name)
        rows.append({
            "skill": skill_dir.name,
            "script_count": len(scripts),
            "scripts_referenced_by_tests": referenced_scripts,
            "skill_has_deterministic_test_reference": directly_referenced,
            "designed_eval_cases": case_counts.get(skill_dir.name, 0),
            "capability_fixture_kind": fixture.get("fixture_kind") if fixture else None,
            "fixture_expected_behavior": fixture.get("expected") if fixture else None,
            "coverage_gate": bool(scripts and directly_referenced and fixture and case_counts.get(skill_dir.name, 0) >= 10),
            "scientific_quality_status": "requires_blind_execution_and_independent_rating",
        })
    missing_skills = sorted(set(fixture_rows) - {row["skill"] for row in rows})
    return {
        "schema_version": "1.0",
        "scope": "Capability evidence inventory. Coverage gates show whether an ability has a script/contract test and designed evaluation cases; they are not scientific-quality scores.",
        "skill_count": len(rows),
        "coverage_ready_count": sum(row["coverage_gate"] for row in rows),
        "missing_fixture_skill_names": missing_skills,
        "skills": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["skill_count"] == 20 and result["coverage_ready_count"] == 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
