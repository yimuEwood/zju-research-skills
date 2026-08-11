#!/usr/bin/env python3
"""Score structural skill quality; this does not judge scientific correctness."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PATH_PATTERN = re.compile(r"`((?:references|scripts)/[^`\s]+)`")


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return {}
    block = text.split("---", 2)[1]
    result: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def audit(skill_dir: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return {"skill": skill_dir.name, "score": 0, "grade": "F", "findings": [{"severity": "error", "message": "SKILL.md is missing"}]}
    text = skill_file.read_text(encoding="utf-8")
    metadata = frontmatter(text)

    description_score = 25
    if list(metadata) != ["name", "description"]:
        description_score -= 12
        findings.append({"severity": "error", "message": "Frontmatter must contain only name and description in order."})
    description = metadata.get("description", "")
    if not 100 <= len(description) <= 700:
        description_score -= 5
        findings.append({"severity": "warning", "message": "Description length is outside the 100-700 character portfolio range."})
    if "Use" not in description:
        description_score -= 4
        findings.append({"severity": "warning", "message": "Description does not state an explicit use boundary."})
    if metadata.get("name") != skill_dir.name:
        description_score -= 4
        findings.append({"severity": "error", "message": "Frontmatter name does not match directory name."})

    organization_score = 30
    line_count = len(text.splitlines())
    if line_count >= 160:
        organization_score -= 12
        findings.append({"severity": "warning", "message": f"SKILL.md has {line_count} lines; move detail into references."})
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir() or not any(refs_dir.iterdir()):
        organization_score -= 10
        findings.append({"severity": "error", "message": "references/ is missing or empty."})
    if (skill_dir / "README.md").exists():
        organization_score -= 8
        findings.append({"severity": "warning", "message": "Skill-local README.md duplicates the entrypoint surface."})

    style_score = 20
    if "## Workflow" not in text and "## Four-Route Workflow" not in text:
        style_score -= 8
        findings.append({"severity": "warning", "message": "No explicit workflow section."})
    if "## Output Contract" not in text:
        style_score -= 8
        findings.append({"severity": "error", "message": "Output contract is missing."})
    if not re.search(r"(?m)^1\. ", text):
        style_score -= 4
        findings.append({"severity": "warning", "message": "Workflow is not expressed as an ordered sequence."})

    structure_score = 25
    if not (skill_dir / "agents/openai.yaml").is_file():
        structure_score -= 8
        findings.append({"severity": "error", "message": "agents/openai.yaml is missing."})
    missing_refs = []
    for relative in sorted(set(PATH_PATTERN.findall(text))):
        cleaned = relative.rstrip(".,:;)")
        if not (skill_dir / cleaned).is_file():
            missing_refs.append(cleaned)
    if missing_refs:
        structure_score -= min(17, 4 * len(missing_refs))
        findings.append({"severity": "error", "message": "Referenced files missing: " + ", ".join(missing_refs)})

    score = max(0, description_score) + max(0, organization_score) + max(0, style_score) + max(0, structure_score)
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "F"
    return {
        "skill": skill_dir.name,
        "score": score,
        "grade": grade,
        "dimensions": {
            "description": max(0, description_score),
            "organization": max(0, organization_score),
            "style": max(0, style_score),
            "structure": max(0, structure_score),
        },
        "findings": findings,
    }


def run(minimum: int) -> dict[str, Any]:
    reports = [audit(path) for path in sorted(SKILLS.iterdir()) if path.is_dir()]
    errors = [item for report in reports for item in report["findings"] if item["severity"] == "error"]
    valid = bool(reports) and not errors and all(report["score"] >= minimum for report in reports)
    return {
        "valid": valid,
        "minimum_score": minimum,
        "scope": "Structural and instructional quality only; scientific task quality requires blind evaluation.",
        "reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum", type=int, default=80)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.minimum)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
\n