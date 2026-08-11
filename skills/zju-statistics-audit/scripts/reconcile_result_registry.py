#!/usr/bin/env python3
"""Validate canonical results and reconcile values reused across research artifacts."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any


CONSUMER_TYPES = {"abstract", "results", "figure", "table", "supplement", "review_response", "data_availability"}
RESULT_KINDS = {"inferential", "descriptive", "diagnostic"}
RESULT_STATUS = {"preliminary", "verified", "superseded"}
P_VALUE_OPERATORS = {"<", "<=", "=", ">=", ">"}


def finding(path: str, message: str, severity: str = "error") -> dict[str, str]:
    return {"path": path, "severity": severity, "message": message}


def _get_path(value: Any, dotted: str) -> tuple[bool, Any]:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(right, ensure_ascii=False, sort_keys=True)
        return str(left).strip() == str(right).strip()


def _render_value(value: Any, format_spec: str | None) -> str:
    if isinstance(value, dict) and set(value) >= {"operator", "value"}:
        return f"{value['operator']}{_render_value(value['value'], format_spec)}"
    if format_spec:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("A numeric format can only be applied to a number.")
        return format(value, format_spec)
    if value is None:
        return "not_applicable"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _valid_p_value(value: Any) -> bool:
    operator = "="
    number = value
    if isinstance(value, dict):
        operator = str(value.get("operator") or "")
        number = value.get("value")
        if operator not in P_VALUE_OPERATORS or set(value) - {"operator", "value"}:
            return False
    try:
        probability = Decimal(str(number))
    except (InvalidOperation, ValueError):
        return False
    return Decimal("0") <= probability <= Decimal("1")


def _analysis_contract_indexes(contract: Any, issues: list[dict[str, str]]) -> tuple[set[str], dict[str, set[str]]]:
    if not isinstance(contract, dict):
        issues.append(finding("analysis_contract", "A validated result registry requires the analysis contract object."))
        return set(), {}
    outcomes = contract.get("outcomes")
    analyses = contract.get("analyses")
    if not isinstance(outcomes, list) or not isinstance(analyses, list):
        issues.append(finding("analysis_contract", "Analysis contract must contain outcomes and analyses lists."))
        return set(), {}
    outcome_ids: set[str] = set()
    for index, outcome in enumerate(outcomes):
        outcome_id = str(outcome.get("outcome_id") or "") if isinstance(outcome, dict) else ""
        if not outcome_id or outcome_id in outcome_ids:
            issues.append(finding(f"analysis_contract.outcomes[{index}].outcome_id", "Missing or duplicate outcome ID."))
        outcome_ids.add(outcome_id)
    analysis_outcomes: dict[str, set[str]] = {}
    for index, analysis in enumerate(analyses):
        analysis_id = str(analysis.get("analysis_id") or "") if isinstance(analysis, dict) else ""
        if not analysis_id or analysis_id in analysis_outcomes:
            issues.append(finding(f"analysis_contract.analyses[{index}].analysis_id", "Missing or duplicate analysis ID."))
            continue
        linked = set(map(str, analysis.get("outcome_ids", []))) if isinstance(analysis.get("outcome_ids"), list) else set()
        unknown = linked - outcome_ids
        if not linked or unknown:
            issues.append(finding(f"analysis_contract.analyses[{index}].outcome_ids", f"Missing or unknown outcome IDs: {sorted(unknown)}"))
        analysis_outcomes[analysis_id] = linked
    return outcome_ids, analysis_outcomes


def validate(payload: dict[str, Any], analysis_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    for field in ("schema_version", "study_id", "analysis_contract_id", "registry_version"):
        if payload.get(field) in (None, ""):
            issues.append(finding(field, "Required registry field is missing."))
    contract = analysis_contract if analysis_contract is not None else payload.get("analysis_contract")
    known_outcomes, analysis_outcomes = _analysis_contract_indexes(contract, issues)
    if isinstance(contract, dict):
        if payload.get("analysis_contract_id") != contract.get("contract_id"):
            issues.append(finding("analysis_contract_id", "Registry contract ID does not match the supplied analysis contract."))
        if payload.get("study_id") and contract.get("study_id") and payload.get("study_id") != contract.get("study_id"):
            issues.append(finding("study_id", "Registry study ID does not match the supplied analysis contract."))
    results = payload.get("results")
    uses = payload.get("uses")
    if not isinstance(results, list) or not results:
        results = []
        issues.append(finding("results", "At least one canonical result is required."))
    if not isinstance(uses, list):
        uses = []
        issues.append(finding("uses", "Uses must be a list."))

    result_by_id: dict[str, dict[str, Any]] = {}
    for index, result in enumerate(results):
        path = f"results[{index}]"
        if not isinstance(result, dict):
            issues.append(finding(path, "Result must be an object."))
            continue
        result_id = str(result.get("result_id") or "")
        if not result_id:
            issues.append(finding(path + ".result_id", "Required field is missing."))
        elif result_id in result_by_id:
            issues.append(finding(path + ".result_id", f"Duplicate result ID: {result_id}"))
        else:
            result_by_id[result_id] = result
        for field in ("analysis_id", "outcome_id", "result_kind", "analysis_population", "source_anchor", "status"):
            if result.get(field) in (None, ""):
                issues.append(finding(path + f".{field}", "Required field is missing."))
        analysis_id = str(result.get("analysis_id") or "")
        outcome_id = str(result.get("outcome_id") or "")
        if analysis_id and analysis_id not in analysis_outcomes:
            issues.append(finding(path + ".analysis_id", f"Unknown analysis ID: {analysis_id}"))
        if outcome_id and outcome_id not in known_outcomes:
            issues.append(finding(path + ".outcome_id", f"Unknown outcome ID: {outcome_id}"))
        elif analysis_id in analysis_outcomes and outcome_id not in analysis_outcomes[analysis_id]:
            issues.append(finding(path + ".outcome_id", f"Outcome {outcome_id} is not declared for analysis {analysis_id}."))
        if result.get("result_kind") not in RESULT_KINDS:
            issues.append(finding(path + ".result_kind", "Unsupported result kind."))
        if result.get("status") not in RESULT_STATUS:
            issues.append(finding(path + ".status", "Unsupported result status."))
        if result.get("result_kind") == "inferential":
            for field in ("effect_measure", "estimate", "unit", "direction", "multiplicity_status"):
                if result.get(field) in (None, ""):
                    issues.append(finding(path + f".{field}", "Inferential result field is missing."))
            ci = result.get("ci")
            if not isinstance(ci, dict) or any(ci.get(field) is None for field in ("level", "lower", "upper")):
                issues.append(finding(path + ".ci", "Inferential results require interval level, lower, and upper values."))
            else:
                try:
                    lower = Decimal(str(ci["lower"]))
                    upper = Decimal(str(ci["upper"]))
                    estimate = Decimal(str(result.get("estimate")))
                    if lower > upper:
                        issues.append(finding(path + ".ci", "Interval lower bound exceeds upper bound."))
                    if not lower <= estimate <= upper:
                        issues.append(finding(path + ".estimate", "Estimate lies outside its reported interval."))
                    level = Decimal(str(ci["level"]))
                    if not Decimal("0") < level < Decimal("1"):
                        issues.append(finding(path + ".ci.level", "Interval level must lie between 0 and 1."))
                except (InvalidOperation, ValueError):
                    issues.append(finding(path + ".ci", "Estimate and interval values must be numeric."))
            if result.get("p_value") is not None:
                if not _valid_p_value(result["p_value"]):
                    issues.append(finding(path + ".p_value", "P value must be numeric, null, or {operator, value} with a valid bound."))
            n = result.get("n")
            if not isinstance(n, dict) or n.get("experimental_units") in (None, ""):
                issues.append(finding(path + ".n.experimental_units", "Report the experimental-unit sample size."))
            else:
                for field in ("experimental_units", "observations"):
                    if n.get(field) is not None and (not isinstance(n[field], int) or isinstance(n[field], bool) or n[field] <= 0):
                        issues.append(finding(path + f".n.{field}", "Sample count must be a positive integer."))
        if not isinstance(result.get("diagnostics"), list):
            issues.append(finding(path + ".diagnostics", "Diagnostics must be a list, even when not applicable."))
        if not isinstance(result.get("sensitivity_analyses"), list):
            issues.append(finding(path + ".sensitivity_analyses", "Sensitivity analyses must be a list, even when not applicable."))

    seen_use_ids: set[str] = set()
    used_result_ids: set[str] = set()
    mismatches = 0
    compared = 0
    for index, use in enumerate(uses):
        path = f"uses[{index}]"
        if not isinstance(use, dict):
            issues.append(finding(path, "Use must be an object."))
            continue
        use_id = str(use.get("use_id") or "")
        if not use_id:
            issues.append(finding(path + ".use_id", "Required field is missing."))
        elif use_id in seen_use_ids:
            issues.append(finding(path + ".use_id", f"Duplicate use ID: {use_id}"))
        seen_use_ids.add(use_id)
        if use.get("consumer_type") not in CONSUMER_TYPES:
            issues.append(finding(path + ".consumer_type", "Unsupported consumer type."))
        if not use.get("anchor"):
            issues.append(finding(path + ".anchor", "Consumer anchor is required."))
        result_id = str(use.get("result_id") or "")
        result = result_by_id.get(result_id)
        if result is None:
            issues.append(finding(path + ".result_id", f"Unknown result ID: {result_id or '<missing>'}"))
            continue
        used_result_ids.add(result_id)
        if result.get("status") == "superseded":
            issues.append(finding(path + ".result_id", f"Consumer references superseded result {result_id}."))
        elif result.get("status") == "preliminary":
            issues.append(finding(path + ".result_id", f"Consumer references preliminary result {result_id}.", "warning"))
        values = use.get("values")
        if not isinstance(values, dict) or not values:
            issues.append(finding(path + ".values", "Record every canonical value rendered by this consumer."))
            continue
        for field, reported in values.items():
            exists, canonical = _get_path(result, str(field))
            compared += 1
            if not exists:
                mismatches += 1
                issues.append(finding(path + f".values.{field}", "Field does not exist in the canonical result."))
            elif isinstance(reported, dict) and "rendered" in reported:
                format_spec = reported.get("format")
                if format_spec is not None and not isinstance(format_spec, str):
                    mismatches += 1
                    issues.append(finding(path + f".values.{field}.format", "Format must be a Python numeric format string or null."))
                    continue
                try:
                    expected = _render_value(canonical, format_spec)
                except (ValueError, TypeError) as exc:
                    mismatches += 1
                    issues.append(finding(path + f".values.{field}", str(exc)))
                    continue
                if str(reported.get("rendered")) != expected:
                    mismatches += 1
                    issues.append(finding(path + f".values.{field}.rendered", f"Rendered value {reported.get('rendered')!r} differs from expected {expected!r}."))
            elif not _equal(canonical, reported):
                mismatches += 1
                issues.append(finding(path + f".values.{field}", f"Reported value {reported!r} differs from canonical value {canonical!r}."))

    errors = [item for item in issues if item["severity"] == "error"]
    return {
        "valid": not errors,
        "results": len(results),
        "uses": len(uses),
        "compared_fields": compared,
        "mismatches": mismatches,
        "consistency_rate": 1.0 if compared == 0 else (compared - mismatches) / compared,
        "unused_result_ids": sorted(set(result_by_id) - used_result_ids),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--analysis-contract", type=Path, help="Analysis contract JSON; overrides an embedded analysis_contract object.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    contract = json.loads(args.analysis_contract.read_text(encoding="utf-8-sig")) if args.analysis_contract else None
    result = validate(payload, analysis_contract=contract)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
