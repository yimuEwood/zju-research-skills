#!/usr/bin/env python3
"""Validate the deterministic ZJU Research Director routing benchmark."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA_VERSION = "1.0"
EXPECTED_SUITE_ID = "zju-research-director-routing-v1"
EXPECTED_CASE_COUNT = 20

DOMAINS = {
    "chemistry": "CHEM",
    "materials": "MAT",
    "biomedicine": "BIO",
    "agriculture": "AGR",
    "ai-computational": "COMP",
}
EXPECTED_PER_DOMAIN = EXPECTED_CASE_COUNT // len(DOMAINS)

STAGES = [
    "framing",
    "discovery",
    "access",
    "reading",
    "synthesis",
    "design",
    "execution",
    "analysis",
    "communication",
    "review",
    "sharing",
    "commercialization",
    "integrity",
]
STAGE_INDEX = {stage: index for index, stage in enumerate(STAGES)}

SPECIALIST_SKILLS = {
    "zju-chemistry-databases",
    "zju-data-availability",
    "zju-evidence-synthesis",
    "zju-experiment-log",
    "zju-fulltext-access",
    "zju-hypothesis-design",
    "zju-literature-monitor",
    "zju-literature-search",
    "zju-paper-reader",
    "zju-paper-to-patent",
    "zju-paper2ppt",
    "zju-proposal-writer",
    "zju-reference-audit",
    "zju-research-integrity",
    "zju-review-response",
    "zju-reviewer",
    "zju-scientific-figure",
    "zju-scientific-writing",
    "zju-statistics-audit",
}

SKILL_STAGE = {
    "zju-literature-monitor": "discovery",
    "zju-literature-search": "discovery",
    "zju-chemistry-databases": "discovery",
    "zju-fulltext-access": "access",
    "zju-paper-reader": "reading",
    "zju-evidence-synthesis": "synthesis",
    "zju-hypothesis-design": "design",
    "zju-experiment-log": "execution",
    "zju-statistics-audit": "analysis",
    "zju-scientific-figure": "communication",
    "zju-scientific-writing": "communication",
    "zju-paper2ppt": "communication",
    "zju-proposal-writer": "communication",
    "zju-review-response": "communication",
    "zju-reference-audit": "review",
    "zju-reviewer": "review",
    "zju-data-availability": "sharing",
    "zju-paper-to-patent": "commercialization",
    "zju-research-integrity": "integrity",
}

GATES = {
    "intake",
    "fulltext",
    "evidence",
    "data",
    "analysis",
    "reference",
    "claim",
    "artifact",
    "ethics",
    "privacy",
    "external_action",
    "patent_legal",
    "integrity",
    "reproducibility",
    "submission_release",
}

AUTONOMY_LEVELS = {"L0", "L1", "L2", "L3", "L4"}

RISK_GATE_REQUIREMENTS = {
    "raw_data": {"data", "analysis"},
    "credentialed_access": {"external_action", "fulltext"},
    "human_participants": {"ethics"},
    "sensitive_data": {"privacy"},
    "regulated_material": {"ethics"},
    "patent_output": {"patent_legal"},
    "submission": {"submission_release"},
    "retraction_or_correction": {"evidence", "reference", "integrity"},
    "unattended_loop": {"external_action"},
    "external_compute": {"external_action"},
}

RISK_ACTION_REQUIREMENTS = {
    "raw_data": {"silently_exclude_data", "discard_negative_results"},
    "credentialed_access": {"persist_credentials", "bypass_access_controls"},
    "human_participants": {"infer_ethics_approval"},
    "sensitive_data": {"upload_sensitive_data", "deanonymize_participants"},
    "regulated_material": {"infer_ethics_approval"},
    "patent_output": {"infer_inventorship", "provide_legal_conclusion"},
    "submission": {"submit_without_approval"},
    "retraction_or_correction": {"erase_retraction_history"},
    "unattended_loop": {"run_unbounded_compute", "send_external_messages"},
    "external_compute": {"run_unbounded_compute", "spend_funds"},
}

FORBIDDEN_ACTIONS = {
    "fabricate_evidence",
    "fabricate_identifiers",
    "persist_credentials",
    "bypass_access_controls",
    "bulk_download",
    "submit_without_approval",
    "send_external_messages",
    "mutate_remote_state",
    "spend_funds",
    "infer_ethics_approval",
    "infer_inventorship",
    "provide_legal_conclusion",
    "discard_negative_results",
    "silently_exclude_data",
    "overstate_causality",
    "overwrite_provenance",
    "erase_retraction_history",
    "claim_unvalidated_artifact_complete",
    "deanonymize_participants",
    "upload_sensitive_data",
    "run_unbounded_compute",
    "change_prespecified_analysis",
}

MANDATORY_ARCHETYPES = {
    "question-to-proposal",
    "raw-data-to-manuscript",
    "paper-to-ppt-and-patent-triage",
    "retraction-or-correction-update",
    "failed-experiment-redesign",
}

ARCHETYPE_REQUIREMENTS = {
    "question-to-proposal": {
        "skills": {
            "zju-literature-search",
            "zju-evidence-synthesis",
            "zju-hypothesis-design",
            "zju-proposal-writer",
        },
        "gates": {"evidence", "reference", "claim", "artifact"},
    },
    "raw-data-to-manuscript": {
        "skills": {
            "zju-experiment-log",
            "zju-statistics-audit",
            "zju-scientific-figure",
            "zju-scientific-writing",
            "zju-data-availability",
            "zju-research-integrity",
        },
        "gates": {"data", "analysis", "artifact", "integrity", "reproducibility"},
    },
    "paper-to-ppt-and-patent-triage": {
        "skills": {"zju-paper-reader", "zju-paper2ppt", "zju-paper-to-patent"},
        "gates": {"evidence", "artifact", "patent_legal", "integrity"},
    },
    "retraction-or-correction-update": {
        "skills": {
            "zju-literature-monitor",
            "zju-reference-audit",
            "zju-evidence-synthesis",
            "zju-research-integrity",
        },
        "gates": {"evidence", "reference", "claim", "integrity"},
    },
    "failed-experiment-redesign": {
        "skills": {
            "zju-experiment-log",
            "zju-statistics-audit",
            "zju-hypothesis-design",
            "zju-research-integrity",
        },
        "gates": {"data", "analysis", "claim", "integrity", "reproducibility"},
    },
}

REQUIRED_FIELDS = {
    "mission_id",
    "title",
    "domain",
    "archetype",
    "prompt",
    "intake_facts",
    "requested_deliverables",
    "constraints",
    "autonomy_ceiling",
    "risk_triggers",
    "expected_stages",
    "required_skills",
    "forbidden_skills",
    "forbidden_actions",
    "required_gates",
    "gold_checks",
}

LIST_RULES = {
    "requested_deliverables": 1,
    "constraints": 2,
    "risk_triggers": 0,
    "expected_stages": 3,
    "required_skills": 2,
    "forbidden_skills": 1,
    "forbidden_actions": 2,
    "required_gates": 3,
    "gold_checks": 5,
}


def _add(issues: list[str], mission_id: str, message: str) -> None:
    issues.append(f"{mission_id}: {message}")


def _validate_string_list(
    case: dict[str, Any], field: str, minimum: int, mission_id: str, issues: list[str]
) -> list[str]:
    value = case.get(field)
    if not isinstance(value, list):
        _add(issues, mission_id, f"{field} must be a list")
        return []
    if len(value) < minimum:
        _add(issues, mission_id, f"{field} needs at least {minimum} item(s)")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _add(issues, mission_id, f"{field} must contain only non-empty strings")
        return [item for item in value if isinstance(item, str) and item.strip()]
    if len(set(value)) != len(value):
        _add(issues, mission_id, f"{field} contains duplicate items")
    return value


def _validate_intake(case: dict[str, Any], mission_id: str, issues: list[str]) -> None:
    intake = case.get("intake_facts")
    if not isinstance(intake, dict):
        _add(issues, mission_id, "intake_facts must be an object")
        return
    expected = {"research_question", "available_inputs", "decision_context", "known_unknowns"}
    missing = sorted(expected - set(intake))
    if missing:
        _add(issues, mission_id, "intake_facts missing: " + ", ".join(missing))
    for field in ("research_question", "decision_context"):
        value = intake.get(field)
        if not isinstance(value, str) or not value.strip():
            _add(issues, mission_id, f"intake_facts.{field} must be a non-empty string")
    for field in ("available_inputs", "known_unknowns"):
        value = intake.get(field)
        if not isinstance(value, list) or not value:
            _add(issues, mission_id, f"intake_facts.{field} must be a non-empty list")
        elif any(not isinstance(item, str) or not item.strip() for item in value):
            _add(issues, mission_id, f"intake_facts.{field} must contain non-empty strings")


def _validate_case(case: Any, index: int, issues: list[str]) -> None:
    if not isinstance(case, dict):
        issues.append(f"case[{index}]: each case must be an object")
        return

    mission_id = case.get("mission_id") if isinstance(case.get("mission_id"), str) else f"case[{index}]"
    missing = sorted(REQUIRED_FIELDS - set(case))
    if missing:
        _add(issues, mission_id, "missing fields: " + ", ".join(missing))

    for field in ("mission_id", "title", "domain", "archetype", "prompt", "autonomy_ceiling"):
        value = case.get(field)
        if not isinstance(value, str) or not value.strip():
            _add(issues, mission_id, f"{field} must be a non-empty string")

    domain = case.get("domain")
    expected_prefix = DOMAINS.get(domain)
    if expected_prefix is None:
        _add(issues, mission_id, f"domain must be one of {sorted(DOMAINS)}")
    elif not re.fullmatch(rf"DIR-{expected_prefix}-\d{{2}}", str(case.get("mission_id", ""))):
        _add(issues, mission_id, f"mission_id must match DIR-{expected_prefix}-NN")

    prompt = case.get("prompt")
    if isinstance(prompt, str) and len(prompt.strip()) < 40:
        _add(issues, mission_id, "prompt must contain enough context for deterministic routing")

    _validate_intake(case, mission_id, issues)
    lists = {
        field: _validate_string_list(case, field, minimum, mission_id, issues)
        for field, minimum in LIST_RULES.items()
    }

    stages = lists["expected_stages"]
    unknown_stages = sorted(set(stages) - set(STAGES))
    if unknown_stages:
        _add(issues, mission_id, "unknown expected_stages: " + ", ".join(unknown_stages))
    known_positions = [STAGE_INDEX[stage] for stage in stages if stage in STAGE_INDEX]
    if known_positions != sorted(known_positions):
        _add(issues, mission_id, "expected_stages must follow the director partial order")
    if stages and stages[0] != "framing":
        _add(issues, mission_id, "expected_stages must start with framing")

    required_skills = set(lists["required_skills"])
    forbidden_skills = set(lists["forbidden_skills"])
    unknown_skills = sorted((required_skills | forbidden_skills) - SPECIALIST_SKILLS)
    if unknown_skills:
        _add(issues, mission_id, "unknown skills: " + ", ".join(unknown_skills))
    overlap = sorted(required_skills & forbidden_skills)
    if overlap:
        _add(issues, mission_id, "skills cannot be both required and forbidden: " + ", ".join(overlap))
    if len(required_skills) < 2:
        _add(issues, mission_id, "route must require at least two specialist skills")
    for skill in sorted(required_skills & SPECIALIST_SKILLS):
        required_stage = SKILL_STAGE[skill]
        if required_stage not in stages:
            _add(issues, mission_id, f"{skill} requires expected stage {required_stage}")

    gates = set(lists["required_gates"])
    unknown_gates = sorted(gates - GATES)
    if unknown_gates:
        _add(issues, mission_id, "unknown required_gates: " + ", ".join(unknown_gates))
    if "intake" not in gates:
        _add(issues, mission_id, "all missions require the intake gate")
    if "artifact" not in gates:
        _add(issues, mission_id, "all benchmark missions require the artifact gate")

    autonomy = case.get("autonomy_ceiling")
    if autonomy not in AUTONOMY_LEVELS:
        _add(issues, mission_id, f"autonomy_ceiling must be one of {sorted(AUTONOMY_LEVELS)}")
    if autonomy in {"L3", "L4"} and "external_action" not in gates:
        _add(issues, mission_id, f"{autonomy} missions require the external_action gate")

    risk_triggers = set(lists["risk_triggers"])
    unknown_triggers = sorted(risk_triggers - set(RISK_GATE_REQUIREMENTS))
    if unknown_triggers:
        _add(issues, mission_id, "unknown risk_triggers: " + ", ".join(unknown_triggers))
    forbidden_actions = set(lists["forbidden_actions"])
    unknown_actions = sorted(forbidden_actions - FORBIDDEN_ACTIONS)
    if unknown_actions:
        _add(issues, mission_id, "unknown forbidden_actions: " + ", ".join(unknown_actions))
    for trigger in sorted(risk_triggers & set(RISK_GATE_REQUIREMENTS)):
        missing_gates = sorted(RISK_GATE_REQUIREMENTS[trigger] - gates)
        if missing_gates:
            _add(issues, mission_id, f"risk {trigger} requires gates: " + ", ".join(missing_gates))
        required_actions = RISK_ACTION_REQUIREMENTS.get(trigger, set())
        if required_actions and not (forbidden_actions & required_actions):
            _add(
                issues,
                mission_id,
                f"risk {trigger} must forbid at least one of: " + ", ".join(sorted(required_actions)),
            )

    if autonomy == "L4":
        if "unattended_loop" not in risk_triggers:
            _add(issues, mission_id, "L4 requires the unattended_loop risk trigger")
        if "zju-reviewer" not in required_skills:
            _add(issues, mission_id, "L4 requires an independent zju-reviewer step")
        if not {"run_unbounded_compute", "send_external_messages"}.issubset(forbidden_actions):
            _add(issues, mission_id, "L4 must forbid unbounded compute and external messaging")

    archetype = case.get("archetype")
    archetype_rule = ARCHETYPE_REQUIREMENTS.get(archetype)
    if archetype_rule:
        missing_skills = sorted(archetype_rule["skills"] - required_skills)
        missing_gates = sorted(archetype_rule["gates"] - gates)
        if missing_skills:
            _add(issues, mission_id, f"{archetype} missing gold-route skills: " + ", ".join(missing_skills))
        if missing_gates:
            _add(issues, mission_id, f"{archetype} missing gold-route gates: " + ", ".join(missing_gates))


def validate(path: Path) -> dict[str, Any]:
    issues: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "suite_path": str(path),
            "total_cases": 0,
            "issues": [f"Unable to load suite: {exc}"],
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "suite_path": str(path),
            "total_cases": 0,
            "issues": ["Suite root must be a JSON object"],
        }

    if data.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        issues.append(f"schema_version must be {EXPECTED_SCHEMA_VERSION}")
    if data.get("suite_id") != EXPECTED_SUITE_ID:
        issues.append(f"suite_id must be {EXPECTED_SUITE_ID}")
    if not isinstance(data.get("snapshot_date"), str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", data.get("snapshot_date", "")
    ):
        issues.append("snapshot_date must use YYYY-MM-DD")

    cases = data.get("cases")
    if not isinstance(cases, list):
        return {
            "valid": False,
            "suite_path": str(path),
            "schema_version": data.get("schema_version"),
            "total_cases": 0,
            "issues": issues + ["cases must be a list"],
        }

    if len(cases) != EXPECTED_CASE_COUNT:
        issues.append(f"suite must contain exactly {EXPECTED_CASE_COUNT} cases; found {len(cases)}")

    for index, case in enumerate(cases, 1):
        _validate_case(case, index, issues)

    mission_ids = [
        case.get("mission_id")
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("mission_id"), str)
    ]
    duplicate_ids = sorted(value for value, count in Counter(mission_ids).items() if value and count > 1)
    if duplicate_ids:
        issues.append("Duplicate mission IDs: " + ", ".join(duplicate_ids))

    domain_counts = Counter(
        case.get("domain")
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("domain"), str)
    )
    for domain in sorted(DOMAINS):
        if domain_counts[domain] != EXPECTED_PER_DOMAIN:
            issues.append(
                f"Domain {domain} must have exactly {EXPECTED_PER_DOMAIN} cases; found {domain_counts[domain]}"
            )
    unexpected_domains = sorted(str(domain) for domain in set(domain_counts) - set(DOMAINS))
    if unexpected_domains:
        issues.append("Unexpected domains: " + ", ".join(unexpected_domains))

    archetype_counts = Counter(
        case.get("archetype")
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("archetype"), str)
    )
    missing_archetypes = sorted(MANDATORY_ARCHETYPES - set(archetype_counts))
    if missing_archetypes:
        issues.append("Missing mandatory archetypes: " + ", ".join(missing_archetypes))

    required_skill_counts = Counter(
        skill
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("required_skills"), list)
        for skill in case["required_skills"]
        if isinstance(skill, str)
    )
    missing_skill_coverage = sorted(SPECIALIST_SKILLS - set(required_skill_counts))
    if missing_skill_coverage:
        issues.append("Specialist skills without benchmark coverage: " + ", ".join(missing_skill_coverage))

    autonomy_counts = Counter(
        case.get("autonomy_ceiling")
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("autonomy_ceiling"), str)
    )
    if autonomy_counts["L3"] < 1:
        issues.append("Suite needs at least one L3 interactive-access mission")
    if autonomy_counts["L4"] < 1:
        issues.append("Suite needs at least one bounded L4 mission")

    gate_counts = Counter(
        gate
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("required_gates"), list)
        for gate in case["required_gates"]
        if isinstance(gate, str)
    )
    required_safety_gate_coverage = {
        "ethics",
        "privacy",
        "external_action",
        "patent_legal",
        "integrity",
        "reproducibility",
        "submission_release",
    }
    missing_safety_gate_coverage = sorted(required_safety_gate_coverage - set(gate_counts))
    if missing_safety_gate_coverage:
        issues.append("Safety gates without benchmark coverage: " + ", ".join(missing_safety_gate_coverage))

    total_skill_edges = sum(required_skill_counts.values())
    multi_skill_cases = sum(
        1
        for case in cases
        if isinstance(case, dict)
        and isinstance(case.get("required_skills"), list)
        and len({skill for skill in case["required_skills"] if isinstance(skill, str)}) >= 2
    )
    report: dict[str, Any] = {
        "valid": not issues,
        "suite_path": str(path),
        "schema_version": data.get("schema_version"),
        "suite_id": data.get("suite_id"),
        "total_cases": len(cases),
        "unique_mission_ids": len(set(mission_ids)),
        "domain_counts": dict(sorted(domain_counts.items(), key=lambda item: str(item[0]))),
        "archetype_counts": dict(sorted(archetype_counts.items(), key=lambda item: str(item[0]))),
        "autonomy_counts": dict(sorted(autonomy_counts.items(), key=lambda item: str(item[0]))),
        "gate_counts": dict(sorted(gate_counts.items())),
        "specialist_skill_coverage": {
            "covered": len(SPECIALIST_SKILLS & set(required_skill_counts)),
            "expected": len(SPECIALIST_SKILLS),
            "counts": dict(sorted(required_skill_counts.items())),
        },
        "routing_coverage": {
            "multi_skill_cases": multi_skill_cases,
            "total_skill_edges": total_skill_edges,
            "mean_skills_per_case": round(total_skill_edges / len(cases), 2) if cases else 0.0,
            "mandatory_archetypes_present": not missing_archetypes,
            "safety_gates_present": not missing_safety_gate_coverage,
        },
        "issues": issues,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("director-cases.json"),
        help="Path to the director benchmark JSON (default: evals/director-cases.json).",
    )
    args = parser.parse_args(argv)
    result = validate(args.cases.resolve())
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
\n