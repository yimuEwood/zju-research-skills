#!/usr/bin/env python3
"""Validate a design-first statistical analysis contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


OUTCOME_ROLES = {"primary", "secondary", "exploratory", "safety", "quality_control"}
DESIGN_STAGES = {"prospective", "frozen", "amended", "retrospective"}


def finding(path: str, message: str, severity: str = "error") -> dict[str, str]:
    return {"path": path, "severity": severity, "message": message}


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    for field in ("contract_id", "study_id", "question", "design_stage"):
        if not payload.get(field):
            issues.append(finding(field, "Required field is missing."))
    if payload.get("design_stage") and payload["design_stage"] not in DESIGN_STAGES:
        issues.append(finding("design_stage", "Unsupported design stage."))

    design = payload.get("design")
    if not isinstance(design, dict):
        design = {}
        issues.append(finding("design", "Design must be an object."))
    for field in ("experimental_unit", "observational_unit", "assignment", "grouping_structure"):
        if not design.get(field):
            issues.append(finding(f"design.{field}", "Required design field is missing."))
    if design.get("assignment") == "randomized" and not design.get("randomization_unit"):
        issues.append(finding("design.randomization_unit", "Randomized designs must identify the randomization unit."))
    technical = design.get("technical_replicates")
    if technical not in (None, 0, False, "none") and not design.get("technical_replicate_aggregation"):
        issues.append(finding("design.technical_replicate_aggregation", "Define how technical replicates become one experimental-unit value."))

    outcomes = payload.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        outcomes = []
        issues.append(finding("outcomes", "At least one outcome is required."))
    outcome_by_id: dict[str, dict[str, Any]] = {}
    for index, outcome in enumerate(outcomes):
        path = f"outcomes[{index}]"
        if not isinstance(outcome, dict):
            issues.append(finding(path, "Outcome must be an object."))
            continue
        outcome_id = str(outcome.get("outcome_id") or "")
        if not outcome_id:
            issues.append(finding(path + ".outcome_id", "Required field is missing."))
        elif outcome_id in outcome_by_id:
            issues.append(finding(path + ".outcome_id", f"Duplicate outcome ID: {outcome_id}"))
        else:
            outcome_by_id[outcome_id] = outcome
        for field in ("role", "variable", "scale", "timepoint"):
            if outcome.get(field) in (None, ""):
                issues.append(finding(path + f".{field}", "Required field is missing."))
        if outcome.get("role") not in OUTCOME_ROLES:
            issues.append(finding(path + ".role", "Unsupported outcome role."))

    analyses = payload.get("analyses")
    if not isinstance(analyses, list) or not analyses:
        analyses = []
        issues.append(finding("analyses", "At least one planned analysis is required."))
    analysis_ids: set[str] = set()
    covered_outcomes: set[str] = set()
    dependent = bool(design.get("repeated_measures") or design.get("clusters") or design.get("nesting"))
    for index, analysis in enumerate(analyses):
        path = f"analyses[{index}]"
        if not isinstance(analysis, dict):
            issues.append(finding(path, "Analysis must be an object."))
            continue
        analysis_id = str(analysis.get("analysis_id") or "")
        if not analysis_id:
            issues.append(finding(path + ".analysis_id", "Required field is missing."))
        elif analysis_id in analysis_ids:
            issues.append(finding(path + ".analysis_id", f"Duplicate analysis ID: {analysis_id}"))
        analysis_ids.add(analysis_id)
        outcome_ids = analysis.get("outcome_ids")
        if not _nonempty_list(outcome_ids):
            issues.append(finding(path + ".outcome_ids", "Map the analysis to at least one outcome."))
            outcome_ids = []
        for outcome_id in map(str, outcome_ids):
            if outcome_id not in outcome_by_id:
                issues.append(finding(path + ".outcome_ids", f"Unknown outcome ID: {outcome_id}"))
            covered_outcomes.add(outcome_id)
        for field in (
            "estimand", "analysis_population", "model_family", "effect_measure",
            "uncertainty", "missing_data_strategy", "multiplicity_family",
        ):
            if analysis.get(field) in (None, ""):
                issues.append(finding(path + f".{field}", "Required analysis decision is missing."))
        if not _nonempty_list(analysis.get("diagnostics")):
            issues.append(finding(path + ".diagnostics", "Specify model diagnostics and their decision consequences."))
        if not _nonempty_list(analysis.get("sensitivity_analyses")):
            issues.append(finding(path + ".sensitivity_analyses", "Specify at least one robustness or sensitivity analysis."))
        if dependent and not _nonempty_list(analysis.get("grouping_terms")):
            issues.append(finding(path + ".grouping_terms", "Repeated, clustered, or nested data require explicit grouping/correlation terms."))

    for outcome_id, outcome in outcome_by_id.items():
        if outcome.get("role") in {"primary", "secondary"} and outcome_id not in covered_outcomes:
            issues.append(finding("analyses", f"Confirmatory outcome {outcome_id} has no planned analysis."))

    primary_count = sum(item.get("role") == "primary" for item in outcomes if isinstance(item, dict))
    if primary_count == 0:
        issues.append(finding("outcomes", "No primary outcome is declared.", "warning"))
    if primary_count > 1 and not payload.get("primary_multiplicity_strategy"):
        issues.append(finding("primary_multiplicity_strategy", "Multiple primary outcomes require a family-level multiplicity strategy."))

    errors = [item for item in issues if item["severity"] == "error"]
    return {
        "valid": not errors,
        "plan_ready": not errors and payload.get("design_stage") in {"frozen", "amended"},
        "outcomes": len(outcomes),
        "analyses": len(analyses),
        "confirmatory_outcomes_covered": all(
            outcome_id in covered_outcomes
            for outcome_id, outcome in outcome_by_id.items()
            if outcome.get("role") in {"primary", "secondary"}
        ),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8-sig")))
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
