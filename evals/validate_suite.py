#!/usr/bin/env python3
"""Validate evaluation case coverage and required fields."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


FIRST_WAVE_SKILLS = {
    "zju-literature-search",
    "zju-fulltext-access",
    "zju-reference-audit",
    "zju-paper-reader",
    "zju-experiment-log",
    "zju-statistics-audit",
    "zju-scientific-writing",
}
EXPANSION_SKILLS = {
    "zju-literature-monitor",
    "zju-evidence-synthesis",
    "zju-hypothesis-design",
    "zju-scientific-figure",
    "zju-paper2ppt",
    "zju-reviewer",
    "zju-review-response",
    "zju-data-availability",
    "zju-proposal-writer",
    "zju-paper-to-patent",
    "zju-chemistry-databases",
    "zju-research-integrity",
}
REQUIRED = {"id", "skill", "domain", "prompt", "fixture", "gold_checks"}


def validate(path: Path, minimum: int, expected_skills: set[str] | None = None) -> dict[str, object]:
    expected_skills = FIRST_WAVE_SKILLS if expected_skills is None else expected_skills
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = data.get("cases", [])
    issues: list[str] = []
    ids = [case.get("id") for case in cases]
    duplicates = sorted(value for value, count in Counter(ids).items() if value and count > 1)
    if duplicates:
        issues.append("Duplicate case IDs: " + ", ".join(duplicates))
    for index, case in enumerate(cases, 1):
        missing = sorted(REQUIRED - set(case))
        if missing:
            issues.append(f"Case {index} missing fields: {', '.join(missing)}")
        if not isinstance(case.get("gold_checks"), list) or len(case.get("gold_checks", [])) < 3:
            issues.append(f"Case {case.get('id', index)} needs at least three gold checks")
    counts = Counter(case.get("skill") for case in cases)
    for skill in sorted(expected_skills):
        if counts[skill] < minimum:
            issues.append(f"{skill} has {counts[skill]} cases; minimum is {minimum}")
    unexpected = sorted(set(counts) - expected_skills)
    if unexpected:
        issues.append("Unexpected skills: " + ", ".join(str(item) for item in unexpected))
    return {"valid": not issues, "total_cases": len(cases), "counts": dict(sorted(counts.items())), "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--minimum", type=int, default=10)
    parser.add_argument("--scope", choices=("first-wave", "expansion"), default="first-wave")
    args = parser.parse_args()
    expected = FIRST_WAVE_SKILLS if args.scope == "first-wave" else EXPANSION_SKILLS
    cases_path = args.cases or Path(__file__).with_name(
        "cases.json" if args.scope == "first-wave" else "expansion-cases.json"
    )
    result = validate(cases_path, args.minimum, expected)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
\n