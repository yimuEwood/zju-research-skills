#!/usr/bin/env python3
"""Route one registered output to one specialist without creating mission state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class FastRouteError(ValueError):
    pass


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FastRouteError(f"cannot read JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FastRouteError(f"expected a JSON object in {path}")
    return value


def route(
    request: dict,
    registry: dict,
    *,
    installed_optional_skills: list[str] | tuple[str, ...] = (),
) -> dict:
    request_id = request.get("request_id")
    objective = request.get("objective")
    requested_output = request.get("requested_output")
    if not all(isinstance(value, str) and value for value in (request_id, objective, requested_output)):
        raise FastRouteError("request_id, objective, and requested_output must be non-empty strings")
    supplied = request.get("available_inputs", [])
    constraints = request.get("constraints", [])
    if not isinstance(supplied, list) or any(not isinstance(value, str) or not value for value in supplied):
        raise FastRouteError("available_inputs must be a list of non-empty strings")
    if not isinstance(constraints, list) or any(not isinstance(value, str) or not value for value in constraints):
        raise FastRouteError("constraints must be a list of non-empty strings")
    core_skills = registry.get("skills")
    if not isinstance(core_skills, dict):
        raise FastRouteError("capability registry has no skills object")
    skills = dict(core_skills)
    available_optional = request.get("available_optional_skills", [])
    if not isinstance(available_optional, list) or any(
        not isinstance(value, str) or not value for value in available_optional
    ):
        raise FastRouteError("available_optional_skills must be a list of non-empty strings")
    optional = registry.get("optional_skills", {})
    installed_optional = set(installed_optional_skills)
    declared_optional = set(available_optional)
    usable_optional = installed_optional & declared_optional if declared_optional else installed_optional
    if isinstance(optional, dict):
        for skill_id in sorted(usable_optional):
            entry = optional.get(skill_id)
            if isinstance(entry, dict):
                skills[skill_id] = entry
    matches = []
    for skill_id, record in skills.items():
        if isinstance(record, dict) and requested_output in record.get("produces", []):
            matches.append((skill_id, record))
    if not matches:
        raise FastRouteError(f"no specialist produces requested output: {requested_output}")
    if len(matches) > 1:
        raise FastRouteError(
            "requested output is ambiguous across specialists: " + ", ".join(sorted(skill for skill, _ in matches))
        )
    skill_id, record = matches[0]
    required_gates = list(record.get("required_human_gates", []))
    high_risk = record.get("risk_level") in {"high", "critical"}
    status = "escalate" if high_risk or required_gates else "ready"
    reason = None
    if status == "escalate":
        reason = "Fast Mode cannot complete a high-risk or human-gated request; route may be used only for a draft or registered fallback."
    accepted = set(record.get("accepts", []))
    recognized = sorted(set(supplied) & accepted)
    required_groups = record.get("fast_required_input_groups", [])
    if not isinstance(required_groups, list) or any(
        not isinstance(group, list)
        or not group
        or any(not isinstance(value, str) or not value for value in group)
        for group in required_groups
    ):
        raise FastRouteError(f"{skill_id} has invalid fast_required_input_groups")
    missing_groups = [sorted(group) for group in required_groups if not (set(group) & set(recognized))]
    if not required_groups and not recognized:
        missing_groups = [sorted(accepted)] if accepted else [["task_input"]]
    if status == "ready" and missing_groups:
        status = "needs_input"
        reason = "Fast Mode selected one specialist, but the supplied inputs are not sufficient to run it."
    return {
        "schema_version": "1.0",
        "mode": "fast",
        "request_id": request_id,
        "objective": objective,
        "status": status,
        "selected_specialist": skill_id,
        "requested_output": requested_output,
        "input_packet": {
            "recognized_inputs": recognized,
            "unrecognized_inputs": sorted(set(supplied) - accepted),
            "missing_required_input_groups": missing_groups,
            "constraints": constraints,
        },
        "specialist_contract": {
            "accepts": record.get("accepts", []),
            "produces": record.get("produces", []),
            "fallback": record.get("fallback"),
            "risk_level": record.get("risk_level"),
            "required_human_gates": required_gates,
        },
        "execution_claim": "not_executed",
        "escalation_reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--registry",
        default=str(Path(__file__).resolve().parents[1] / "references" / "capability-registry.yaml"),
    )
    args = parser.parse_args(argv)
    try:
        request = _load(Path(args.input))
        registry_path = Path(args.registry)
        registry = _load(registry_path)
        installed_optional_skills: list[str] = []
        if registry_path.name == "capability-registry.yaml":
            skill_root = registry_path.resolve().parents[2]
            optional = registry.get("optional_skills", {})
            if isinstance(optional, dict):
                installed_optional_skills = sorted(
                    skill_id for skill_id in optional if (skill_root / skill_id / "SKILL.md").is_file()
                )
        result = route(request, registry, installed_optional_skills=installed_optional_skills)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, FastRouteError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
