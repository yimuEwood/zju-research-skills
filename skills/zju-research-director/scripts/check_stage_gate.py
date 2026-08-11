#!/usr/bin/env python3
"""Evaluate Research Mission stage gates without performing external actions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import DEFAULT_SCHEMA_PATH, load_document, load_json_yaml, load_registry, write_document  # noqa: E402


HUMAN_AUTHORITY = {
    "ethics": "authorized ethics body",
    "privacy": "data controller or authorized institutional route",
    "external_action": "user or designated system owner",
    "patent_legal": "patent professional or technology-transfer office",
    "submission_release": "corresponding author, PI, or authorized submitter",
}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _approved_decision(mission: dict[str, Any], decision_types: set[str]) -> dict[str, Any] | None:
    for decision in mission.get("decisions", []):
        if not isinstance(decision, dict) or decision.get("status") != "approved":
            continue
        decision_type = str(decision.get("decision_type") or decision.get("gate") or "")
        if decision_type in decision_types:
            return decision
    return None


def _artifacts(mission: dict[str, Any], types: set[str], statuses: set[str] | None = None) -> list[dict[str, Any]]:
    statuses = statuses or {"created", "validated"}
    return [
        artifact
        for artifact in mission.get("artifacts", [])
        if isinstance(artifact, dict)
        and str(artifact.get("artifact_type", "")).lower().replace("-", "_") in types
        and artifact.get("status") in statuses
    ]


def _result(
    gate: str,
    blockers: list[str],
    warnings: list[str],
    required_actions: list[str],
    checked_inputs: list[str],
    human_required: bool = False,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if blockers:
        status = "blocked"
    elif human_required and decision is None:
        status = "requires_human_review"
    else:
        status = "passed"
    return {
        "valid": True,
        "gate": gate,
        "status": status,
        "passed": status == "passed",
        "checked_inputs": checked_inputs,
        "blockers": blockers,
        "warnings": warnings,
        "required_actions": required_actions,
        "human_authority": HUMAN_AUTHORITY.get(gate),
        "decision_id": decision.get("decision_id") if decision else None,
    }


def _evidence_gate(mission: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    evidence = mission.get("evidence_records", [])
    if not isinstance(evidence, list) or not evidence:
        blockers.append("No evidence records are registered")
        evidence = []
    for index, record in enumerate(evidence):
        if not isinstance(record, dict):
            blockers.append(f"Evidence record {index + 1} is not structured")
            continue
        evidence_id = record.get("evidence_id", f"index-{index}")
        if not _nonempty(record.get("evidence_id")):
            blockers.append(f"Evidence record {evidence_id} lacks a stable evidence_id")
        locators = ("doi", "pmid", "arxiv_id", "openalex_id", "identifier", "source_url", "stable_url")
        if not any(_nonempty(record.get(field)) for field in locators):
            blockers.append(f"Evidence {evidence_id} lacks a stable identifier or source URL")
        if str(record.get("version_status", "")).lower() == "retracted":
            warnings.append(f"Evidence {evidence_id} is retracted and requires downstream claim review")
        if not record.get("retrieval_date") and not record.get("retrieved_at"):
            warnings.append(f"Evidence {evidence_id} lacks retrieval provenance")
    return _result("evidence", blockers, warnings, ["Add or repair source-identified evidence records"] if blockers else [], ["evidence_records"])


def _claim_gate(mission: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    evidence = {
        item.get("evidence_id"): item
        for item in mission.get("evidence_records", [])
        if isinstance(item, dict) and _nonempty(item.get("evidence_id"))
    }
    claims = mission.get("claims", [])
    if not isinstance(claims, list) or not claims:
        blockers.append("No claim ledger is registered")
        claims = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            blockers.append(f"Claim {index + 1} is not structured")
            continue
        claim_id = claim.get("claim_id", f"index-{index}")
        status = claim.get("status")
        linked = claim.get("evidence_ids", []) if isinstance(claim.get("evidence_ids", []), list) else []
        if status != "supported":
            blockers.append(f"Claim {claim_id} is not in supported state ({status})")
        if not linked:
            blockers.append(f"Claim {claim_id} has no evidence IDs")
        missing = [identifier for identifier in linked if identifier not in evidence]
        if missing:
            blockers.append(f"Claim {claim_id} references unknown evidence: {', '.join(missing)}")
        if not _nonempty(claim.get("source_anchor")) and not claim.get("source_anchors"):
            blockers.append(f"Claim {claim_id} lacks a source anchor")
        for identifier in linked:
            if str(evidence.get(identifier, {}).get("version_status", "")).lower() == "retracted":
                blockers.append(f"Claim {claim_id} relies on retracted evidence {identifier}")
        if claim.get("certainty") == "causal" and claim.get("evidence_design") in {"cross_sectional", "observational"}:
            warnings.append(f"Claim {claim_id} may exceed the supplied study design")
    return _result("claim", blockers, warnings, ["Resolve unsupported claims and add source anchors"] if blockers else [], ["claims", "evidence_records"])


def _artifact_gate(mission: dict[str, Any], artifact_ids: list[str] | None = None) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    artifacts = mission.get("artifacts", [])
    if artifact_ids is not None and isinstance(artifacts, list):
        requested = set(artifact_ids)
        artifacts = [item for item in artifacts if isinstance(item, dict) and item.get("artifact_id") in requested]
        missing = requested - {item.get("artifact_id") for item in artifacts}
        if missing:
            blockers.append("Target artifacts are missing: " + ", ".join(sorted(missing)))
    if not isinstance(artifacts, list) or not artifacts:
        blockers.append("No artifacts are registered")
        artifacts = []
    validated = [item for item in artifacts if isinstance(item, dict) and item.get("status") == "validated"]
    if not validated:
        blockers.append("No artifact has validated status")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            blockers.append("Artifact ledger contains an unstructured item")
            continue
        artifact_id = artifact.get("artifact_id", "UNKNOWN")
        if artifact.get("status") in {"planned", "blocked"}:
            blockers.append(f"Artifact {artifact_id} is only {artifact.get('status')}")
        if artifact.get("status") in {"created", "validated"} and not artifact.get("provenance"):
            warnings.append(f"Artifact {artifact_id} lacks provenance")
    return _result("artifact", blockers, warnings, ["Create and validate every required deliverable artifact"] if blockers else [], ["artifacts"])


def _integrity_gate(mission: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    for risk in mission.get("risks", []):
        if not isinstance(risk, dict) or risk.get("status") in {"mitigated", "closed"}:
            continue
        risk_id = risk.get("risk_id", "UNKNOWN")
        if risk.get("severity") == "critical":
            blockers.append(f"Critical integrity risk remains open: {risk_id}")
        elif risk.get("severity") == "high":
            warnings.append(f"High risk remains unresolved: {risk_id}")
    for loop in mission.get("open_loops", []):
        if isinstance(loop, dict) and loop.get("blocking") and loop.get("status") != "resolved":
            blockers.append(f"Blocking open loop remains unresolved: {loop.get('loop_id', 'UNKNOWN')}")
    return _result("integrity", blockers, warnings, ["Preserve evidence and resolve critical integrity risks"] if blockers else [], ["risks", "open_loops", "artifacts"])


def check_gate(mission: dict[str, Any], gate: str | dict[str, Any], registry_path: str | Path | None = None) -> dict[str, Any]:
    if not isinstance(mission, dict):
        raise ValueError("Mission root must be an object")
    gate_spec = gate if isinstance(gate, dict) else {}
    gate_name = gate_spec.get("gate") if isinstance(gate, dict) else gate
    if not isinstance(gate_name, str):
        return {"valid": False, "gate": gate_name, "status": "invalid", "passed": False, "blockers": ["Gate name must be a string"], "warnings": [], "required_actions": []}
    gate_name = gate_name.lower().replace("-", "_")
    allowed = set(load_json_yaml(DEFAULT_SCHEMA_PATH).get("enums", {}).get("gates", []))
    if gate_name not in allowed:
        return {"valid": False, "gate": gate_name, "status": "invalid", "passed": False, "blockers": [f"Unknown gate: {gate_name}"], "warnings": [], "required_actions": []}
    if registry_path:
        load_registry(registry_path)

    if gate_name == "intake":
        blockers = []
        if not _nonempty(mission.get("research_question")) or mission.get("research_question") == "TO_BE_FRAMED":
            blockers.append("Research question is not framed")
        if not mission.get("objectives"):
            blockers.append("Objectives are missing")
        if not mission.get("requested_deliverables"):
            blockers.append("Requested deliverables are missing")
        if not isinstance(mission.get("constraints"), dict):
            blockers.append("Constraints are missing")
        return _result(gate_name, blockers, [], ["Complete decision-critical intake fields"] if blockers else [], ["research_question", "objectives", "requested_deliverables", "constraints"])

    if gate_name == "evidence":
        return _evidence_gate(mission)
    if gate_name == "claim":
        return _claim_gate(mission)
    if gate_name == "artifact":
        artifact_ids = gate_spec.get("artifact_ids") if isinstance(gate_spec.get("artifact_ids"), list) else None
        return _artifact_gate(mission, artifact_ids)

    if gate_name == "fulltext":
        fulltext = _artifacts(mission, {"full_text", "fulltext_pdf", "paper_fulltext"})
        blockers = [] if fulltext else ["No lawful full-text artifact is registered"]
        for artifact in fulltext:
            if artifact.get("lawful_access") is not True:
                blockers.append(f"Full-text artifact {artifact.get('artifact_id')} lacks lawful-access provenance")
        return _result(gate_name, blockers, [], ["Use an OA or authorized interactive access route"] if blockers else [], ["artifacts"])

    if gate_name == "data":
        data = _artifacts(mission, {"raw_data", "derived_data", "data_inventory", "run_manifest", "experiment_log"})
        experiments = mission.get("experiments", [])
        blockers = [] if data or experiments else ["No traceable data or experiment run is registered"]
        warnings = [f"Data artifact {item.get('artifact_id')} lacks provenance" for item in data if not item.get("provenance")]
        return _result(gate_name, blockers, warnings, ["Register source data/run provenance and experimental units"] if blockers else [], ["artifacts", "experiments"])

    if gate_name == "analysis":
        analyses = _artifacts(mission, {"statistics_audit", "analysis_audit", "analysis_result"}, {"validated"})
        blockers = [] if analyses else ["No validated analysis or statistics-audit artifact is registered"]
        blockers.extend(
            f"Critical analysis risk remains open: {risk.get('risk_id', 'UNKNOWN')}"
            for risk in mission.get("risks", [])
            if isinstance(risk, dict) and risk.get("severity") == "critical" and risk.get("status") == "open"
        )
        return _result(gate_name, blockers, [], ["Validate design, analysis, diagnostics, and uncertainty"] if blockers else [], ["artifacts", "risks"])

    if gate_name == "reference":
        audits = _artifacts(mission, {"reference_audit"}, {"validated"})
        claims = [item for item in mission.get("claims", []) if isinstance(item, dict)]
        verified_claims = bool(claims) and all(item.get("citation_verified") is True for item in claims)
        blockers = [] if audits or verified_claims else ["No validated reference audit or claim-level citation verification is registered"]
        return _result(gate_name, blockers, [], ["Verify bibliographic identity and exact claim support"] if blockers else [], ["artifacts", "claims"])

    if gate_name in {"ethics", "privacy", "external_action", "patent_legal"}:
        trigger = True
        decision_types = {gate_name, f"{gate_name}_approval", f"{gate_name}_authorization"}
        if gate_name == "ethics" and not (mission.get("constraints", {}).get("ethics_required") or mission.get("constraints", {}).get("human_or_animal_subjects")):
            trigger = False
        if gate_name == "privacy" and not (mission.get("constraints", {}).get("sensitive_data") or mission.get("constraints", {}).get("personal_data")):
            trigger = False
        decision = _approved_decision(mission, decision_types)
        return _result(
            gate_name,
            [],
            [] if trigger else [f"{gate_name} gate is not triggered by current mission facts"],
            [f"Obtain scoped approval from {HUMAN_AUTHORITY[gate_name]}"] if trigger and not decision else [],
            ["constraints", "decisions"],
            human_required=trigger,
            decision=decision,
        )

    if gate_name == "integrity":
        return _integrity_gate(mission)

    if gate_name == "reproducibility":
        reproducible = _artifacts(mission, {"data_inventory", "run_manifest", "data_availability", "code_manifest"}, {"validated"})
        blockers = [] if reproducible else ["No validated reproducibility inventory or run manifest is registered"]
        blockers.extend(
            f"Blocking open loop remains unresolved: {loop.get('loop_id', 'UNKNOWN')}"
            for loop in mission.get("open_loops", [])
            if isinstance(loop, dict) and loop.get("blocking") and loop.get("status") != "resolved"
        )
        return _result(gate_name, blockers, [], ["Register data/code/material access and environment provenance"] if blockers else [], ["artifacts", "open_loops"])

    if gate_name == "submission_release":
        blockers: list[str] = []
        warnings: list[str] = []
        for subgate in (_evidence_gate(mission), _claim_gate(mission), _artifact_gate(mission), _integrity_gate(mission)):
            blockers.extend(f"{subgate['gate']}: {item}" for item in subgate["blockers"])
            warnings.extend(f"{subgate['gate']}: {item}" for item in subgate["warnings"])
        decision = _approved_decision(mission, {"submission_release", "release_authorization", "submission_authorization"})
        return _result(
            gate_name,
            blockers,
            warnings,
            ["Resolve all upstream blockers and obtain explicit release authorization"] if blockers or not decision else [],
            ["evidence_records", "claims", "artifacts", "risks", "open_loops", "decisions"],
            human_required=True,
            decision=decision,
        )

    return {"valid": False, "gate": gate_name, "status": "invalid", "passed": False, "blockers": ["Gate implementation is missing"], "warnings": [], "required_actions": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check_gate(load_document(args.mission), args.gate, args.registry)
    if args.output:
        write_document(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
