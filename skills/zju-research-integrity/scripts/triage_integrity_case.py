#!/usr/bin/env python3
"""Audit neutral integrity triage, severity scope, and remediation escalation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ORACLE_IDS = {
    "neutral_triage": "integrity_neutrality_contract_v1",
    "scope_severity": "integrity_scope_severity_matrix_v1",
    "remediation_escalation": "integrity_remediation_escalation_v1",
}
KINDS = {"observed_fact", "reported_statement", "anomaly", "interpretation"}
VERDICT = re.compile(r"\b(?:committed misconduct|fraudulent|guilty|fabricated intentionally|deliberately falsified)\b|(?:已经|确定|故意)(?:造假|篡改|学术不端)", re.I)
PROHIBITED_ACTIONS = {"delete_originals", "modify_evidence", "public_accusation", "credential_access", "interview_subjects"}


def expected_severity(signals: dict[str, Any]) -> str:
    if signals.get("immediate_participant_danger") is True or signals.get("immediate_environment_danger") is True:
        return "critical"
    if any(signals.get(field) is True for field in ("data_security_breach", "evidence_loss_risk", "regulated_research")):
        return "high"
    if any(signals.get(field) is True for field in ("active_submission", "authorship_dispute", "citation_integrity")):
        return "medium"
    return "low"


def expected_scope(signals: dict[str, Any]) -> set[str]:
    scope = {"provenance"}
    if signals.get("immediate_participant_danger") or signals.get("regulated_research"):
        scope.add("ethics")
    if signals.get("immediate_environment_danger"):
        scope.add("safety")
    if signals.get("data_security_breach"):
        scope.update({"data_security", "privacy"})
    if signals.get("active_submission") or signals.get("citation_integrity"):
        scope.add("publication")
    if signals.get("authorship_dispute"):
        scope.add("authorship")
    return scope


def _neutral(payload: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    findings: list[dict[str, str]] = []
    items = payload.get("triage_items")
    if not isinstance(items, list) or not items:
        return [{"field": "triage_items", "message": "at least one triage item is required"}], {"items": 0}
    ids: set[str] = set()
    counts = {kind: 0 for kind in KINDS}
    for index, row in enumerate(items):
        prefix = f"triage_items[{index}]"
        if not isinstance(row, dict):
            findings.append({"field": prefix, "message": "triage item must be an object"})
            continue
        item_id, kind, text = str(row.get("item_id") or ""), row.get("kind"), str(row.get("text") or "")
        if not item_id or item_id in ids:
            findings.append({"field": f"{prefix}.item_id", "message": "item_id must be unique and non-empty"})
        ids.add(item_id)
        if kind not in KINDS:
            findings.append({"field": f"{prefix}.kind", "message": f"kind must be one of {sorted(KINDS)}"})
        else:
            counts[kind] += 1
        if not text.strip() or VERDICT.search(text):
            findings.append({"field": f"{prefix}.text", "message": "triage text must remain descriptive and must not declare intent or misconduct"})
        sources = row.get("source_ids")
        if not isinstance(sources, list) or not sources:
            findings.append({"field": f"{prefix}.source_ids", "message": "every triage item requires traceable source IDs"})
        if kind == "reported_statement" and not str(row.get("attributed_to") or "").strip():
            findings.append({"field": f"{prefix}.attributed_to", "message": "reported statements must identify their attribution"})
    return findings, {"items": len(items), "kind_counts": counts}


def _scope(payload: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    findings: list[dict[str, str]] = []
    signals = payload.get("signals")
    if not isinstance(signals, dict):
        return [{"field": "signals", "message": "signals must be an object"}], {"computed_severity": None, "required_scope": []}
    non_boolean = sorted(field for field, value in signals.items() if not isinstance(value, bool))
    if non_boolean:
        findings.append({"field": "signals", "message": f"all supplied signals must be booleans: {non_boolean}"})
    severity = expected_severity(signals)
    scope = expected_scope(signals)
    if payload.get("declared_severity") != severity:
        findings.append({"field": "declared_severity", "message": f"declared {payload.get('declared_severity')!r}, computed {severity!r}"})
    declared_scope = payload.get("declared_scope")
    if not isinstance(declared_scope, list):
        findings.append({"field": "declared_scope", "message": "declared_scope must be a list"})
        declared_scope = []
    missing = sorted(scope - {str(item) for item in declared_scope})
    if missing:
        findings.append({"field": "declared_scope", "message": f"required scope domains omitted: {missing}"})
    return findings, {"computed_severity": severity, "required_scope": sorted(scope)}


def _remediation(payload: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    findings: list[dict[str, str]] = []
    severity = expected_severity(payload.get("signals") if isinstance(payload.get("signals"), dict) else {})
    actions = payload.get("proposed_actions")
    if not isinstance(actions, list):
        return [{"field": "proposed_actions", "message": "proposed_actions must be a list"}], {"required_actions": []}
    action_rows = [row for row in actions if isinstance(row, dict)]
    if len(action_rows) != len(actions):
        findings.append({"field": "proposed_actions", "message": "every action must be an object"})
    action_names = {str(row.get("action") or "") for row in action_rows}
    prohibited = sorted(action_names & PROHIBITED_ACTIONS)
    if prohibited:
        findings.append({"field": "proposed_actions", "message": f"prohibited actions: {prohibited}"})
    required = {"preserve_evidence", "restrict_access"}
    if severity in {"medium", "high", "critical"}:
        required.add("document_chronology")
    if severity in {"high", "critical"}:
        required.add("authorized_referral")
    if severity == "critical":
        required.add("emergency_channel")
    missing = sorted(required - action_names)
    if missing:
        findings.append({"field": "proposed_actions", "message": f"required actions omitted for {severity} severity: {missing}"})
    for index, row in enumerate(action_rows):
        if row.get("action") in {"authorized_referral", "emergency_channel"} and not str(row.get("authority") or "").strip():
            findings.append({"field": f"proposed_actions[{index}].authority", "message": "escalation action requires an identified authorized role or channel"})
    return findings, {"severity": severity, "required_actions": sorted(required), "prohibited_actions": prohibited}


def audit(payload: dict[str, Any], check: str = "all") -> dict[str, Any]:
    checks = list(ORACLE_IDS) if check == "all" else [check]
    if any(item not in ORACLE_IDS for item in checks):
        return {"oracle_id": None, "valid": False, "check": check, "findings": [{"field": "check", "message": "unknown integrity check"}]}
    findings: list[dict[str, str]] = []
    details: dict[str, Any] = {}
    functions = {"neutral_triage": _neutral, "scope_severity": _scope, "remediation_escalation": _remediation}
    for item in checks:
        item_findings, item_details = functions[item](payload)
        findings.extend({**finding, "check": item} for finding in item_findings)
        details[item] = item_details
    oracle_id: str | dict[str, str] = ORACLE_IDS[checks[0]] if len(checks) == 1 else {item: ORACLE_IDS[item] for item in checks}
    return {"oracle_id": oracle_id, "valid": not findings, "check": check, "details": details, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--check", choices=["all", *ORACLE_IDS], default="all")
    args = parser.parse_args()
    result = audit(json.loads(args.input.read_text(encoding="utf-8-sig")), args.check)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
