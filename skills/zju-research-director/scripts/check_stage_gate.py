#!/usr/bin/env python3
"""Evaluate Research Mission stage gates without performing external actions."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import DEFAULT_SCHEMA_PATH, load_document, load_json_yaml, load_registry, stable_hash, write_document  # noqa: E402
from artifact_contract import validate_artifact  # noqa: E402
from authorization_contract import verify_trusted_receipt  # noqa: E402
from plan_mission import audit_route_contract, required_release_output_groups  # noqa: E402


HUMAN_AUTHORITY = {
    "ethics": "authorized ethics body",
    "privacy": "data controller or authorized institutional route",
    "external_action": "user or designated system owner",
    "patent_legal": "patent professional or technology-transfer office",
    "submission_release": "corresponding author, PI, or authorized submitter",
}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_timestamp(value: Any) -> datetime | None:
    if not _nonempty(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def authorization_state_hash(mission: dict[str, Any], gate: str) -> str:
    """Hash the gate-relevant state without including authorization decisions themselves."""

    payload: dict[str, Any] = {
        "mission_id": mission.get("mission_id"),
        "gate": gate,
        "constraints": mission.get("constraints", {}),
        "requested_deliverables": mission.get("requested_deliverables", []),
    }
    if gate == "submission_release":
        payload.update(
            {
                "route_contract": mission.get("route_contract"),
                "route_contract_sha256": mission.get("route_contract_sha256"),
                "route_execution": [
                    {
                        "step_id": step.get("step_id"),
                        "skill": step.get("skill"),
                        "stage": step.get("stage"),
                        "prerequisites": step.get("prerequisites", []),
                        "expected_outputs": step.get("expected_outputs", []),
                        "required_output_groups": step.get("required_output_groups", []),
                        "required_gates": step.get("required_gates", []),
                        "status": step.get("status"),
                        "produced_artifact_ids": step.get("produced_artifact_ids", []),
                    }
                    for step in mission.get("route", [])
                    if isinstance(step, dict)
                ],
                "reused_validated_artifacts": mission.get("reused_validated_artifacts", []),
                "artifacts": [
                    {
                        key: artifact.get(key)
                        for key in ("artifact_id", "artifact_type", "status", "content_sha256")
                    }
                    for artifact in mission.get("artifacts", [])
                    if isinstance(artifact, dict)
                ],
                "claims": mission.get("claims", []),
                "evidence_records": mission.get("evidence_records", []),
                "risks": mission.get("risks", []),
                "open_loops": mission.get("open_loops", []),
            }
        )
    return stable_hash(payload)


def _authorization_decision(
    mission: dict[str, Any],
    gate: str,
    decision_types: set[str],
    trusted_receipts: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    candidates = []
    for decision in mission.get("decisions", []):
        if not isinstance(decision, dict) or decision.get("status") != "approved":
            continue
        decision_type = str(decision.get("decision_type") or decision.get("gate") or "")
        if decision_type in decision_types:
            candidates.append(decision)
    issues: list[str] = []
    for decision in reversed(candidates):
        current: list[str] = []
        for field in ("decision_id", "authorized_by", "authority_role", "target", "action", "state_sha256"):
            if not _nonempty(decision.get(field)):
                current.append(f"authorization decision lacks {field}")
        scope = decision.get("scope")
        if not isinstance(scope, dict):
            current.append("authorization scope must be an object")
            scope = {}
        if scope.get("mission_id") != mission.get("mission_id"):
            current.append("authorization scope does not match mission_id")
        if gate == "submission_release":
            scoped_ids = scope.get("artifact_ids")
            constraints = mission.get("constraints", {}) if isinstance(mission.get("constraints"), dict) else {}
            current_ids = constraints.get("release_artifact_ids", [])
            if not isinstance(current_ids, list) or not current_ids:
                current.append("mission does not declare constraints.release_artifact_ids")
            elif not isinstance(scoped_ids, list) or sorted(scoped_ids) != sorted(current_ids):
                current.append("release authorization must scope exactly constraints.release_artifact_ids")
        issued_at = _parse_timestamp(decision.get("issued_at"))
        expires_at = _parse_timestamp(decision.get("expires_at"))
        if issued_at is None:
            current.append("authorization issued_at must be a timezone-aware ISO-8601 timestamp")
        if expires_at is None:
            current.append("authorization expires_at must be a timezone-aware ISO-8601 timestamp")
        if issued_at is not None and expires_at is not None:
            if expires_at <= issued_at:
                current.append("authorization expires_at must be later than issued_at")
            if expires_at <= datetime.now(timezone.utc):
                current.append("authorization has expired")
        expected_hash = authorization_state_hash(mission, gate)
        if decision.get("state_sha256") != expected_hash:
            current.append("authorization is not bound to the current gate-relevant state")
        if not current:
            constraints = mission.get("constraints", {}) if isinstance(mission.get("constraints"), dict) else {}
            release_ids = constraints.get("release_artifact_ids", []) if gate == "submission_release" else None
            receipt, receipt_issues = verify_trusted_receipt(
                decision,
                mission_id=str(mission.get("mission_id") or ""),
                gate=gate,
                expected_state_sha256=expected_hash,
                trusted_receipts=trusted_receipts,
                expected_artifact_ids=release_ids if isinstance(release_ids, list) else [],
            )
            if receipt is not None:
                return decision, []
            current.extend(receipt_issues)
        issues.extend(f"{decision.get('decision_id', 'UNKNOWN')}: {item}" for item in current)
    return None, issues


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
        "approval_receipt_id": decision.get("approval_receipt_id") if decision else None,
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


def _artifact_gate(
    mission: dict[str, Any],
    artifact_ids: list[str] | None = None,
    base_dir: str | Path | None = None,
    trusted_validation_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        contract = validate_artifact(
            artifact,
            mission_id=mission.get("mission_id"),
            base_dir=base_dir,
            trusted_validation_receipts=trusted_validation_receipts,
        )
        if not contract["valid"]:
            blockers.append(
                f"Artifact {artifact_id} violates the shared envelope: "
                + "; ".join(item["message"] for item in contract["errors"])
            )
        if artifact.get("status") != "validated":
            blockers.append(f"Artifact {artifact_id} is {artifact.get('status')}, not validated")
        elif not contract.get("content_verified") or not contract.get("validation_receipt_verified"):
            blockers.append(
                f"Artifact {artifact_id} lacks verified local content or independent trusted-runner attestation"
            )
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


def check_gate(
    mission: dict[str, Any],
    gate: str | dict[str, Any],
    registry_path: str | Path | None = None,
    base_dir: str | Path | None = None,
    trusted_receipts: list[dict[str, Any]] | None = None,
    trusted_validation_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        return _artifact_gate(
            mission,
            artifact_ids,
            base_dir=base_dir,
            trusted_validation_receipts=trusted_validation_receipts,
        )

    if gate_name == "fulltext":
        fulltext = _artifacts(mission, {"full_text", "fulltext_pdf", "paper_fulltext"}, {"validated"})
        blockers = [] if fulltext else ["No lawful full-text artifact is registered"]
        if fulltext:
            artifact_result = _artifact_gate(
                mission,
                [item["artifact_id"] for item in fulltext if _nonempty(item.get("artifact_id"))],
                base_dir=base_dir,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            blockers.extend(artifact_result.get("blockers", []))
        for artifact in fulltext:
            if artifact.get("lawful_access") is not True:
                blockers.append(f"Full-text artifact {artifact.get('artifact_id')} lacks lawful-access provenance")
        return _result(gate_name, blockers, [], ["Use an OA or authorized interactive access route"] if blockers else [], ["artifacts"])

    if gate_name == "data":
        data = _artifacts(
            mission,
            {"raw_data", "derived_data", "data_inventory", "run_manifest", "experiment_log"},
            {"validated"},
        )
        blockers = [] if data else ["No validated, content-bound data or run artifact is registered"]
        if data:
            artifact_result = _artifact_gate(
                mission,
                [item["artifact_id"] for item in data if _nonempty(item.get("artifact_id"))],
                base_dir=base_dir,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            blockers.extend(artifact_result.get("blockers", []))
        return _result(gate_name, blockers, [], ["Register and validate a source data/run inventory; record experimental units and exclusions in the specialist artifact"] if blockers else [], ["artifacts"])

    if gate_name == "analysis":
        analyses = _artifacts(mission, {"statistics_audit", "analysis_audit", "analysis_result"}, {"validated"})
        blockers = [] if analyses else ["No validated analysis or statistics-audit artifact is registered"]
        if analyses:
            artifact_result = _artifact_gate(
                mission,
                [item["artifact_id"] for item in analyses if _nonempty(item.get("artifact_id"))],
                base_dir=base_dir,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            blockers.extend(artifact_result.get("blockers", []))
        blockers.extend(
            f"Critical analysis risk remains open: {risk.get('risk_id', 'UNKNOWN')}"
            for risk in mission.get("risks", [])
            if isinstance(risk, dict) and risk.get("severity") == "critical" and risk.get("status") == "open"
        )
        return _result(gate_name, blockers, [], ["Validate design, analysis, diagnostics, and uncertainty"] if blockers else [], ["artifacts", "risks"])

    if gate_name == "reference":
        audits = _artifacts(mission, {"reference_audit"}, {"validated"})
        blockers = [] if audits else ["No validated, content-bound reference audit is registered"]
        if audits:
            artifact_result = _artifact_gate(
                mission,
                [item["artifact_id"] for item in audits if _nonempty(item.get("artifact_id"))],
                base_dir=base_dir,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            blockers.extend(artifact_result.get("blockers", []))
        return _result(gate_name, blockers, [], ["Verify bibliographic identity and exact claim support"] if blockers else [], ["artifacts", "claims"])

    if gate_name in {"ethics", "privacy", "external_action", "patent_legal"}:
        trigger = True
        decision_types = {gate_name, f"{gate_name}_approval", f"{gate_name}_authorization"}
        if gate_name == "ethics" and not (mission.get("constraints", {}).get("ethics_required") or mission.get("constraints", {}).get("human_or_animal_subjects")):
            trigger = False
        if gate_name == "privacy" and not (mission.get("constraints", {}).get("sensitive_data") or mission.get("constraints", {}).get("personal_data")):
            trigger = False
        decision, authorization_issues = _authorization_decision(
            mission,
            gate_name,
            decision_types,
            trusted_receipts=trusted_receipts,
        )
        return _result(
            gate_name,
            [],
            authorization_issues if trigger else [f"{gate_name} gate is not triggered by current mission facts"],
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
        if reproducible:
            artifact_result = _artifact_gate(
                mission,
                [item["artifact_id"] for item in reproducible if _nonempty(item.get("artifact_id"))],
                base_dir=base_dir,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            blockers.extend(artifact_result.get("blockers", []))
        blockers.extend(
            f"Blocking open loop remains unresolved: {loop.get('loop_id', 'UNKNOWN')}"
            for loop in mission.get("open_loops", [])
            if isinstance(loop, dict) and loop.get("blocking") and loop.get("status") != "resolved"
        )
        return _result(gate_name, blockers, [], ["Register data/code/material access and environment provenance"] if blockers else [], ["artifacts", "open_loops"])

    if gate_name == "submission_release":
        blockers: list[str] = []
        warnings: list[str] = []
        constraints = mission.get("constraints", {}) if isinstance(mission.get("constraints"), dict) else {}
        route_audit = audit_route_contract(mission, registry_path)
        blockers.extend(f"route_contract: {issue}" for issue in route_audit["issues"])
        explicit_gates = {
            gate
            for gate in mission.get("mission_required_gates", mission.get("required_gates", []))
            if isinstance(gate, str)
        }
        route_gates = {
            gate
            for step in mission.get("route", [])
            if isinstance(step, dict)
            for gate in step.get("required_gates", [])
            if isinstance(gate, str)
        }
        requested = {
            str(item.get("type") if isinstance(item, dict) else item).lower().replace("-", "_")
            for item in mission.get("requested_deliverables", [])
        }
        full_release = bool(constraints.get("release_intent")) or bool(
            requested & {"submission_package", "manuscript_package", "complete_submission"}
        )
        applicable = ["evidence"]
        if full_release or "data" in explicit_gates | route_gates:
            applicable.append("data")
        if full_release or "analysis" in explicit_gates | route_gates:
            applicable.append("analysis")
        if full_release or "reference" in explicit_gates | route_gates:
            applicable.append("reference")
        applicable.extend(["claim", "artifact", "integrity"])
        if full_release or "reproducibility" in explicit_gates | route_gates:
            applicable.append("reproducibility")
        triggered_human = []
        if constraints.get("ethics_required") or constraints.get("human_or_animal_subjects") or "ethics" in explicit_gates | route_gates:
            triggered_human.append("ethics")
        if constraints.get("sensitive_data") or constraints.get("personal_data") or "privacy" in explicit_gates | route_gates:
            triggered_human.append("privacy")
        if constraints.get("external_mutation") or "external_action" in explicit_gates | route_gates:
            triggered_human.append("external_action")
        if constraints.get("patent_output") or "patent_legal" in explicit_gates | route_gates:
            triggered_human.append("patent_legal")
        applicable.extend(triggered_human)

        required_release_groups = required_release_output_groups(mission.get("requested_deliverables", []))
        if not required_release_groups:
            blockers.append("artifact: requested_deliverables do not define any machine-verifiable release output groups")
        release_artifact_ids = constraints.get("release_artifact_ids")
        if not isinstance(release_artifact_ids, list) or not release_artifact_ids:
            blockers.append("artifact: constraints.release_artifact_ids must declare the exact release payload")
            release_artifact_ids = []
        elif any(not _nonempty(identifier) for identifier in release_artifact_ids):
            blockers.append("artifact: constraints.release_artifact_ids must contain only non-empty artifact IDs")
        elif len(set(release_artifact_ids)) != len(release_artifact_ids):
            blockers.append("artifact: constraints.release_artifact_ids must not contain duplicates")
        release_artifact_ids = [identifier for identifier in release_artifact_ids if _nonempty(identifier)]

        artifact_by_id = {
            item.get("artifact_id"): item
            for item in mission.get("artifacts", [])
            if isinstance(item, dict) and _nonempty(item.get("artifact_id"))
        }
        route_by_id = {
            step.get("step_id"): step
            for step in mission.get("route", [])
            if isinstance(step, dict) and _nonempty(step.get("step_id"))
        }
        if route_audit["valid"]:
            for expected_step in route_audit["expected"]["required_steps"]:
                step_id = expected_step["step_id"]
                step = route_by_id.get(step_id, {})
                status = step.get("status")
                if status not in {"completed", "skipped"}:
                    blockers.append(
                        f"route_contract: required step {step_id} ({expected_step['skill']}) is {status}, not completed or skipped"
                    )
                    continue
                produced_ids = step.get("produced_artifact_ids", [])
                if not isinstance(produced_ids, list) or any(not _nonempty(item) for item in produced_ids):
                    blockers.append(
                        f"route_contract: required step {step_id} lacks valid produced_artifact_ids"
                    )
                    produced_ids = []
                produced_ids = [item for item in produced_ids if _nonempty(item)]
                produced_types = {
                    str(artifact_by_id[item].get("artifact_type", ""))
                    .lower()
                    .replace("-", "_")
                    .replace(" ", "_")
                    for item in produced_ids
                    if item in artifact_by_id
                }
                missing_groups = [
                    group
                    for group in expected_step.get("required_output_groups", [])
                    if not produced_types.intersection(group)
                ]
                if missing_groups:
                    blockers.append(
                        f"route_contract: step {step_id} produced artifacts do not satisfy canonical output groups: "
                        + json.dumps(missing_groups, ensure_ascii=False, separators=(",", ":"))
                    )
                for group in expected_step.get("required_output_groups", []):
                    matching = [
                        artifact_by_id[item]
                        for item in produced_ids
                        if item in artifact_by_id
                        and str(artifact_by_id[item].get("artifact_type", ""))
                        .lower()
                        .replace("-", "_")
                        .replace(" ", "_")
                        in group
                    ]
                    if status == "completed" and matching and not any(
                        isinstance(artifact.get("provenance"), dict)
                        and artifact["provenance"].get("producer") == expected_step["skill"]
                        and artifact["provenance"].get("producer_step_id") == step_id
                        for artifact in matching
                    ):
                        blockers.append(
                            f"route_contract: step {step_id} has no required-output artifact produced by its canonical specialist and step ID"
                        )
                if status == "skipped":
                    reuse_records = [
                        item
                        for item in mission.get("reused_validated_artifacts", [])
                        if isinstance(item, dict) and item.get("skill") == expected_step["skill"]
                    ]
                    if not any(
                        isinstance(item.get("artifact_ids"), list)
                        and set(produced_ids).issubset(set(item["artifact_ids"]))
                        for item in reuse_records
                    ):
                        blockers.append(
                            f"route_contract: skipped step {step_id} lacks a canonical reused-artifact record for produced_artifact_ids"
                        )
                if produced_ids:
                    step_artifact_gate = _artifact_gate(
                        mission,
                        produced_ids,
                        base_dir=base_dir,
                        trusted_validation_receipts=trusted_validation_receipts,
                    )
                    blockers.extend(
                        f"route_contract: step {step_id}: {item}"
                        for item in step_artifact_gate.get("blockers", [])
                    )
        selected_artifacts = [artifact_by_id[identifier] for identifier in release_artifact_ids if identifier in artifact_by_id]
        selected_types = {
            str(item.get("artifact_type", "")).lower().replace("-", "_").replace(" ", "_")
            for item in selected_artifacts
        }
        missing_release_groups = [
            group
            for group in required_release_groups
            if not selected_types.intersection(group)
        ]
        if missing_release_groups:
            blockers.append(
                "artifact: release payload does not satisfy requested-deliverable output groups: "
                + json.dumps(missing_release_groups, ensure_ascii=False, separators=(",", ":"))
            )
        allowed_release_types = {item for group in required_release_groups for item in group}
        unauthorized_release_types = sorted(selected_types - allowed_release_types)
        if unauthorized_release_types:
            blockers.append(
                "artifact: release payload contains types not authorized by requested_deliverables: "
                + ", ".join(unauthorized_release_types)
            )
        subgate_results = [
            _artifact_gate(
                mission,
                release_artifact_ids,
                base_dir=base_dir,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            if gate == "artifact"
            else check_gate(
                mission,
                gate,
                registry_path=registry_path,
                base_dir=base_dir,
                trusted_receipts=trusted_receipts,
                trusted_validation_receipts=trusted_validation_receipts,
            )
            for gate in list(dict.fromkeys(applicable))
        ]
        for subgate in subgate_results:
            if subgate.get("status") != "passed":
                if subgate.get("blockers"):
                    blockers.extend(f"{subgate['gate']}: {item}" for item in subgate["blockers"])
                else:
                    blockers.append(f"{subgate['gate']}: {subgate.get('status', 'not_passed')}")
            warnings.extend(f"{subgate['gate']}: {item}" for item in subgate.get("warnings", []))
        decision, authorization_issues = _authorization_decision(
            mission,
            gate_name,
            {"submission_release", "release_authorization", "submission_authorization"},
            trusted_receipts=trusted_receipts,
        )
        warnings.extend(authorization_issues)
        result = _result(
            gate_name,
            blockers,
            warnings,
            ["Resolve all upstream blockers and obtain explicit release authorization"] if blockers or not decision else [],
            ["evidence_records", "claims", "artifacts", "risks", "open_loops", "decisions"],
            human_required=True,
            decision=decision,
        )
        result["applicable_upstream_gates"] = list(dict.fromkeys(applicable))
        result["required_release_output_groups"] = required_release_groups
        result["release_artifact_ids"] = list(release_artifact_ids)
        result["route_contract_verified"] = route_audit["valid"]
        result["route_contract_sha256"] = route_audit["route_contract_sha256"]
        result["authorization_state_sha256"] = authorization_state_hash(mission, gate_name)
        return result

    return {"valid": False, "gate": gate_name, "status": "invalid", "passed": False, "blockers": ["Gate implementation is missing"], "warnings": [], "required_actions": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument(
        "--approval-receipts",
        type=Path,
        help="trusted caller-supplied JSON receipt list; mission decisions cannot approve themselves",
    )
    parser.add_argument(
        "--trusted-validation-receipts",
        type=Path,
        help="attestations from a caller-authenticated independent validation runner",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    mission_path = args.mission.resolve()
    trusted_receipts = None
    if args.approval_receipts:
        receipt_document = load_document(args.approval_receipts)
        trusted_receipts = (
            receipt_document.get("receipts")
            if isinstance(receipt_document, dict)
            else receipt_document
        )
        if not isinstance(trusted_receipts, list):
            raise ValueError("approval receipt document must be a list or an object with receipts")
    trusted_validation_receipts = None
    if args.trusted_validation_receipts:
        receipt_document = load_document(args.trusted_validation_receipts)
        trusted_validation_receipts = (
            receipt_document.get("attestations")
            if isinstance(receipt_document, dict)
            else receipt_document
        )
        if not isinstance(trusted_validation_receipts, list):
            raise ValueError(
                "trusted validation receipt document must be a list or an object with attestations"
            )
    result = check_gate(
        load_document(mission_path),
        args.gate,
        args.registry,
        base_dir=args.base_dir or mission_path.parent,
        trusted_receipts=trusted_receipts,
        trusted_validation_receipts=trusted_validation_receipts,
    )
    if args.output:
        write_document(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
