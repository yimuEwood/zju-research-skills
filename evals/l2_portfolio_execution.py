#!/usr/bin/env python3
"""Execute the protocol-v3 deterministic portfolio suite.

The suite intentionally measures only reproducible software behavior.  A pass
means that a parser, validator, transformation, rejection path, or artifact
executor matched an explicit oracle.  It is not evidence that free-form
scientific advice is correct.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "evals" / "skill-evaluation-matrix-v3.json"
PROTOCOL_PATH = ROOT / "evals" / "portfolio-protocol-v3.json"
CASES_PATH = ROOT / "evals" / "l2-cases-v3.json"
EVIDENCE_DIR = ROOT / "evals" / "evidence" / "l2-v3"
LAYER = "L2_deterministic_function"
RUN_ID = "l2-deterministic-portfolio-v3"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _seal_log_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sealed: list[dict[str, Any]] = []
    previous = "GENESIS"
    for source in events:
        row = copy.deepcopy(source)
        row["previous_event_hash"] = previous
        material = {key: value for key, value in row.items() if key != "event_hash"}
        row["event_hash"] = "sha256:" + canonical_sha256(material)
        sealed.append(row)
        previous = row["event_hash"]
    return sealed


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
    )
    value = completed.stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("git rev-parse HEAD did not return a full commit")
    return value


def load_module(path: Path):
    module_name = "l2_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import executor: {path}")
    old_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = old_path


def _mission() -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "mission_id": "MISSION-L2-001",
        "research_question": "Which intervention improves the measured outcome?",
        "domain": "materials",
        "objectives": [{"objective_id": "O01", "statement": "Build a falsifiable research plan"}],
        "constraints": {"autonomy_ceiling": "L1"},
        "requested_deliverables": ["hypothesis_set"],
        "current_stage": "framing",
        "status": "draft",
        "evidence_records": [], "claims": [], "hypotheses": [], "experiments": [],
        "artifacts": [], "decisions": [], "risks": [], "open_loops": [], "route": [],
    }


BASES: dict[str, dict[str, Any]] = {
    "zju-literature-search": {
        "records": [
            {"record_id": "REC-1", "title": "A Study", "doi": "10.1000/l2", "source": "Crossref", "query_id": "Q1"},
            {"record_id": "REC-2", "title": "A study.", "DOI": "https://doi.org/10.1000/L2", "database": "OpenAlex", "query_id": "Q2"},
            {"record_id": "REC-3", "title": "Identity-free evidence", "year": 2024, "source": "manual"},
        ]
    },
    "zju-fulltext-access": {
        "record_id": "REC-1", "doi": "10.1000/l2", "oa_url": "https://example.org/paper.pdf",
        "supplement_url": "https://example.org/supp.pdf", "data_url": "https://example.org/data",
        "requested_components": ["full_text", "supplement", "data"], "version_requested": "version_of_record",
    },
    "zju-reference-audit": {"field": "doi", "submitted": "https://doi.org/10.1000/L2.", "canonical": "10.1000/l2"},
    "zju-paper-reader": {"source_text": "# Abstract\nFigure 1 reports the design.\n# Results\nTable 1 reports the measured result and Equation (1).\n", "source_type": "markdown"},
    "zju-experiment-log": {
        "schema_version": "2.0", "run_type": "experiment", "run_id": "RUN-1", "experiment_id": "EXP-1",
        "started_at": "2026-08-10T08:00:00+08:00", "status": "completed",
        "inputs": [{"artifact_id": "RAW-1", "role": "raw_data", "path": "raw/input.csv", "sha256": "a" * 64}],
        "parameters": {"temperature_C": 25}, "software": [{"name": "instrument", "version": "1.0"}],
        "outputs": [{"artifact_id": "OUT-1", "role": "analysis_output", "path": "derived/result.csv", "sha256": "b" * 64, "derived_from": ["RAW-1"]}],
        "design_snapshot": {"experimental_unit": "sample", "observational_unit": "measurement", "outcome_ids": ["Y1"]},
        "analysis_contract": {"contract_id": "AC-1", "status": "frozen", "path": "analysis-contract.json", "sha256": "c" * 64},
        "decision_gate": {"criterion": "QC within range", "decision": "continue", "actor": "operator", "decided_at": "2026-08-10T09:00:00+08:00"},
    },
    "zju-statistics-audit": {
        "rows": [
            {"unit": "C1", "group": "control", "value": 1.0}, {"unit": "C2", "group": "control", "value": 2.0},
            {"unit": "C3", "group": "control", "value": 2.5}, {"unit": "C4", "group": "control", "value": 3.0},
            {"unit": "T1", "group": "treatment", "value": 3.5}, {"unit": "T2", "group": "treatment", "value": 4.0},
            {"unit": "T3", "group": "treatment", "value": 5.0}, {"unit": "T4", "group": "treatment", "value": 6.0},
        ],
        "contract": {
            "contract_id": "AC-L2", "study_id": "STUDY-L2", "question": "What is the mean difference?", "design_stage": "frozen",
            "design": {"experimental_unit": "sample", "observational_unit": "one value", "assignment": "randomized", "randomization_unit": "sample", "grouping_structure": "one row per sample"},
            "outcomes": [{"outcome_id": "OUT-1", "role": "primary", "variable": "value", "scale": "continuous", "timepoint": "endpoint"}],
            "analyses": [{
                "analysis_id": "AN-1", "outcome_ids": ["OUT-1"], "estimand": "treatment minus control mean",
                "analysis_population": "all complete samples", "model_family": "welch_ttest", "effect_measure": "mean difference",
                "uncertainty": "95% confidence interval", "missing_data_strategy": "complete case", "multiplicity_family": "single primary",
                "diagnostics": ["distribution checks"], "sensitivity_analyses": ["rank sensitivity"],
                "execution": {"method": "welch_ttest", "result_id": "RES-1", "value_column": "value", "group_column": "group", "experimental_unit_column": "unit", "reference_group": "control", "comparison_group": "treatment", "unit": "AU", "confidence_level": 0.95},
            }],
        },
    },
    "zju-scientific-writing": {"claims": [{"claim_id": "C1", "claim": "The bounded result is supported.", "claim_type": "literature", "status": "supported", "evidence_ids": ["E1"], "source_anchor": "p. 2", "citation_verified": True}]},
    "zju-literature-monitor": {"state": {"profile_id": "MON-1", "seen": []}, "records": [{"doi": "10.1000/new", "title": "New monitored work", "year": 2026}], "observed_at": "2026-08-15T09:00:00+08:00"},
    "zju-evidence-synthesis": {"rows": [{
        "record_id": "REC-1", "study_id": "S1", "report_id": "R1", "citation_id": "10.1000/l2", "design": "RCT",
        "population_or_system": "adults", "sample_and_unit": "40 participants", "intervention_or_exposure": "A", "comparator": "B",
        "outcome": "Y", "time_point": "week 4", "effect_estimate": 1.2, "uncertainty": "95% CI 1.0 to 1.4",
        "risk_of_bias": "low", "source_anchor": "Table 2",
    }]},
    "zju-hypothesis-design": {
        "hypotheses": [
            {"hypothesis_id": "H1", "mechanism": "M1", "assumptions": ["A"], "evidence_ids": ["E1"], "unique_predictions": ["P1"], "falsifiers": ["F1"], "alternative_ids": ["H2"]},
            {"hypothesis_id": "H2", "mechanism": "M2", "assumptions": ["B"], "evidence_ids": ["E1"], "unique_predictions": ["P2"], "falsifiers": ["F2"], "alternative_ids": ["H1"]},
        ],
        "experiments": [{"experiment_id": "X1", "hypothesis_ids": ["H1", "H2"], "experimental_unit": "sample", "intervention": "perturb", "control": "vehicle", "primary_outcome": "signal", "decision_rule": "predefined contrast"}],
    },
    "zju-scientific-figure": {"rows": [{"unit": f"U{i}", "dose": float(i), "response": 1.0 + 2.0 * i} for i in range(1, 7)]},
    "zju-paper2ppt": {
        "project_id": "D1", "source_id": "doi:10.1000/l2", "paper_type": "discovery", "audience": "group meeting",
        "duration_minutes": 10, "terminology_ledger": {"ABC": "term"},
        "slides": [{"slide_id": "S1", "title": "Question", "claim": "The paper tests X", "source_anchors": ["p.1"], "speaker_notes": "Introduce the question", "estimated_seconds": 60}],
    },
    "zju-reviewer": {
        "mode": "single_review", "assessment_boundary": "Methods and Results only",
        "concerns": [{"concern_id": "R1-M1", "severity": "major", "blocking": True, "axis": "validity", "claim_pointer": "Results 1", "evidence_pointer": "Fig. 1", "concern": "Control missing", "why_it_matters": "Inference is ambiguous", "resolution_test": "Add or justify control"}],
    },
    "zju-review-response": {"items": [{
        "comment_id": "R1.1", "source_role": "reviewer_1", "verbatim_comment": "Add control", "action_type": "manuscript_edit",
        "requested_action": "Clarify control", "evidence_status": "verified", "status": "verified_complete", "response_text": "We clarified it",
        "manuscript_change": "Added control definition", "location": "Methods, paragraph 2",
    }]},
    "zju-data-availability": {"artifacts": [{"artifact_id": "D1", "description": "source data", "supports_claims": ["C1"], "controller": "authors", "access_route": "public_repository", "status": "ready", "repository": "Example Repository", "identifier": "doi:10.1000/data"}]},
    "zju-proposal-writer": {
        "proposal_id": "P1", "mode": "compose", "scheme_status": "official_verified",
        "objectives": [{"objective_id": "O1", "question": "Does X affect Y?", "success_criteria": ["estimate obtained"], "evidence_ids": ["E1"]}],
        "work_packages": [{"work_package_id": "WP1", "objective_ids": ["O1"], "methods": ["experiment"], "outputs": ["dataset"], "milestones": ["M1"], "decision_gate": "quality threshold"}],
        "evidence": [{"evidence_id": "E1"}], "risks": [{"risk_id": "R1"}], "compliance": [{"item": "ethics"}], "submission_ready": True,
    },
    "zju-paper-to-patent": {
        "sources": [{"source_id": "P1", "source_type": "paper", "path": "source.pdf"}],
        "features": [{"feature_id": "FT-1", "normalized_term": "controller", "description": "controls output", "source_ids": ["P1"], "source_anchor": "p. 4, lines 10-12", "support_state": "explicit", "claim_role": "independent", "confidentiality": "unpublished"}],
    },
    "zju-chemistry-databases": {
        "entities": [{"entity_id": "M1", "inchi_key": "ABCDEFGHIJKLMN-ABCDEFGHIJ-A", "identity_status": "resolved"}],
        "records": [{"record_id": "R1", "entity_id": "M1", "property_or_endpoint": "melting point", "value": 100, "unit": "degC", "evidence_type": "experimental", "source_database": "primary literature", "source_anchor": "Table 1"}],
    },
    "zju-research-integrity": {"artifact_text": "sample,value\nA,1\n", "declared_text": "sample,value\nA,1\n"},
    "zju-research-director": _mission(),
}


EXECUTORS: dict[str, tuple[str, str]] = {
    "zju-literature-search": ("skills/zju-literature-search/scripts/normalize_records.py", "literature"),
    "zju-fulltext-access": ("skills/zju-fulltext-access/scripts/classify_access.py", "fulltext"),
    "zju-reference-audit": ("skills/zju-reference-audit/scripts/compare_metadata.py", "reference"),
    "zju-paper-reader": ("skills/zju-paper-reader/scripts/prepare_source.py", "reader"),
    "zju-experiment-log": ("skills/zju-experiment-log/scripts/validate_run_manifest.py", "valid"),
    "zju-statistics-audit": ("skills/zju-statistics-audit/scripts/execute_analysis.py", "statistics"),
    "zju-scientific-writing": ("skills/zju-scientific-writing/scripts/check_claim_ledger.py", "writing"),
    "zju-literature-monitor": ("skills/zju-literature-monitor/scripts/update_monitor_state.py", "monitor"),
    "zju-evidence-synthesis": ("skills/zju-evidence-synthesis/scripts/validate_evidence_table.py", "valid"),
    "zju-hypothesis-design": ("skills/zju-hypothesis-design/scripts/validate_hypothesis_set.py", "valid"),
    "zju-scientific-figure": ("skills/zju-scientific-figure/scripts/render_from_registry.py", "figure"),
    "zju-paper2ppt": ("skills/zju-paper2ppt/scripts/validate_deck_plan.py", "valid"),
    "zju-reviewer": ("skills/zju-reviewer/scripts/validate_review_report.py", "valid"),
    "zju-review-response": ("skills/zju-review-response/scripts/validate_response_tracker.py", "valid"),
    "zju-data-availability": ("skills/zju-data-availability/scripts/validate_data_inventory.py", "valid"),
    "zju-proposal-writer": ("skills/zju-proposal-writer/scripts/validate_proposal_manifest.py", "valid"),
    "zju-paper-to-patent": ("skills/zju-paper-to-patent/scripts/validate_feature_ledger.py", "valid"),
    "zju-chemistry-databases": ("skills/zju-chemistry-databases/scripts/validate_chemistry_records.py", "valid"),
    "zju-research-integrity": ("skills/zju-research-integrity/scripts/audit_provenance_manifest.py", "integrity"),
    "zju-research-director": ("skills/zju-research-director/scripts/validate_mission.py", "valid"),
}


SATURATION_BASE = {
    "required_concepts": ["material", "failure"],
    "required_source_families": ["broad_index", "domain_database"],
    "rounds": [
        {"round_id": "R1", "strategy": "core_query", "source_family": "broad_index", "record_ids": ["A", "B"], "eligible_ids": ["A"], "concepts_covered": ["material"]},
        {"round_id": "R2", "strategy": "forward_chaining", "source_family": "domain_database", "record_ids": ["A"], "eligible_ids": [], "concepts_covered": ["failure"]},
        {"round_id": "R3", "strategy": "contradiction_query", "source_family": "broad_index", "record_ids": ["C"], "eligible_ids": ["C"], "concepts_covered": ["material", "failure"]},
    ],
    "evidence_gaps": [],
}

READER_CARD_BASE = {
    "mode": "paper-card",
    "content": "# Source identity\n# Coverage\n# Research question\n# Methods\n# Argument spine\n# Claim-evidence map\nclaim_id: REC-1-C1\n# Primary quantitative findings\n# Robustness, contradictions, and alternative explanations\n# Limitations\n# Evidence-synthesis handoff\n# Figure inventory\n# Table inventory\n# Equation inventory\n# Source anchors\n[p. 2, Results]\n",
}

STATUS_BASE = {
    "records": [{
        "audit_id": "REF-1",
        "status_checked_at": "2026-08-14",
        "status_sources": ["Crossref"],
        "version_status": "version_of_record",
    }],
    "as_of": "2026-08-15", "max_age_days": 30,
}

WRITING_CONTRACT_BASE = {
    "workflow_version": "2.0", "known_result_ids": ["RES-1"],
    "contributions": [{"contribution_id": "K1", "statement": "A bounded contribution", "need": "A defined gap", "evidence_ids": ["E1"], "claim_boundary": "Tested system only", "status": "confirmed"}],
    "results_sections": [{"section_id": "R1", "contribution_ids": ["K1"], "evidence_ids": ["E1"], "result_ids": ["RES-1"]}],
    "reviewer_objections": [{"objection_id": "O1", "risk": "External validity is bounded", "disposition": "accepted_limitation", "response": "State the tested boundary."}],
    "submission_ready": True,
}

EVIDENCE_BUNDLE_BASE = {
    "rows": [
        {**BASES["zju-evidence-synthesis"]["rows"][0], "claim_id": "CLM-1", "evidence_role": "supports", "directness": "direct", "result_direction": "benefit", "measurement_method": "assay-A"},
        {**BASES["zju-evidence-synthesis"]["rows"][0], "record_id": "REC-2", "study_id": "S2", "report_id": "R2", "citation_id": "10.1000/l2b", "claim_id": "CLM-1", "evidence_role": "contradicts", "directness": "direct", "result_direction": "harm", "measurement_method": "assay-B"},
    ],
    "claims": [{"claim_id": "CLM-1", "certainty": "low", "supporting_study_ids": ["S1"], "contradicting_study_ids": ["S2"], "contextual_study_ids": []}],
    "conflicts": [{"conflict_id": "CF-1", "claim_id": "CLM-1", "study_ids": ["S1", "S2"]}],
}

_APPEND_ENTRY = _seal_log_events([{
    "event_id": "EV-1", "event_type": "entry", "timestamp": "2026-08-15T09:00:00+08:00",
    "payload": {"temperature_C": 25, "observation": "clear solution"},
}])[0]
_APPEND_CORRECTION_EVENTS = _seal_log_events([
    {key: value for key, value in _APPEND_ENTRY.items() if key not in {"previous_event_hash", "event_hash"}},
    {
        "event_id": "EV-2", "event_type": "correction", "timestamp": "2026-08-15T09:05:00+08:00",
        "supersedes_event_id": "EV-1", "original_event_hash": _APPEND_ENTRY["event_hash"],
        "patch": {"temperature_C": 26}, "reason": "calibrated thermometer correction",
    },
])
_APPEND_CROSS_EVENTS = _seal_log_events([
    {
        "event_id": "EV-X1", "event_type": "entry", "timestamp": "2026-08-16T09:00:00+08:00",
        "payload": {"pressure_kPa": 101.3, "observation": "stable"},
    },
    {
        "event_id": "EV-X2", "event_type": "correction", "timestamp": "2026-08-16T09:10:00+08:00",
        "supersedes_event_id": "EV-X1", "original_event_hash": "PENDING",
        "patch": {"pressure_kPa": 101.4}, "reason": "instrument zero correction",
    },
])
# Bind the cross correction to the first sealed event, then reseal the chain.
_cross_raw = [{key: value for key, value in row.items() if key not in {"previous_event_hash", "event_hash"}} for row in _APPEND_CROSS_EVENTS]
_cross_raw[1]["original_event_hash"] = _APPEND_CROSS_EVENTS[0]["event_hash"]
_APPEND_CROSS_EVENTS = _seal_log_events(_cross_raw)
_APPEND_TAMPERED_EVENTS = copy.deepcopy(_APPEND_CORRECTION_EVENTS)
_APPEND_TAMPERED_EVENTS[1]["patch"]["temperature_C"] = 99

PROBE_BASES: dict[str, dict[str, Any]] = {
    "saturation": SATURATION_BASE,
    "reader_card": READER_CARD_BASE,
    "status": STATUS_BASE,
    "writing_contract": WRITING_CONTRACT_BASE,
    "evidence_bundle": EVIDENCE_BUNDLE_BASE,
    "claim_support": {
        "evidence_facts": [{"fact_id": "F1", "key": "effect", "value": 2.4, "unit": "mg", "source_id": "REC-1", "source_anchor": "Table 2, row 1"}],
        "claims": [{"claim_id": "C1", "atoms": [{"atom_id": "A1", "key": "effect", "value": 2.4, "unit": "mg", "evidence_fact_ids": ["F1"], "declared_status": "supported", "material": True}]}],
    },
    # Start from one sealed entry so every append-only variant performs a real
    # capability mutation (the positive case appends a correction).
    "append_log": {"events": [_APPEND_ENTRY]},
    "writing_mode": {
        "mode": "polish", "declared_mode": "polish",
        "source_assertions": [{"assertion_id": "A1", "subject": "treatment", "predicate": "reduced", "object": "signal", "value": 2.4, "unit": "AU", "direction": "decrease", "citation_ids": ["REC-1"]}],
        "output_assertions": [{"assertion_id": "A1", "subject": "treatment", "predicate": "reduced", "object": "signal", "value": 2.4, "unit": "AU", "direction": "decrease", "citation_ids": ["REC-1"]}],
    },
    "protocol_selection": {"objective": "focused_answer", "comprehensive_search_required": True, "deadline_days": None, "compatible_effect_estimates_available": False, "declared_protocol": "systematic_review"},
    "data_statement": {
        "inventory": [{"artifact_id": "D1", "supports_claims": True, "access": "public", "persistent_identifier": "doi:10.1000/data", "license": "CC-BY-4.0"}],
        "statement_entries": [{"artifact_id": "D1", "access": "public", "persistent_identifier": "doi:10.1000/data", "license": "CC-BY-4.0"}],
    },
    "proposal_feasibility": {
        "project_weeks": 8,
        "resource_capacities": {"researcher": 1.0},
        "work_packages": [
            {"wp_id": "WP1", "start_week": 1, "end_week": 2, "dependencies": [], "resource_demand": {"researcher": 1.0}, "owner": "PI", "critical": True},
            {"wp_id": "WP2", "start_week": 3, "end_week": 5, "dependencies": ["WP1"], "resource_demand": {"researcher": 1.0}, "owner": "RA", "critical": False},
        ],
        "milestones": [{"milestone_id": "M1", "wp_id": "WP1", "due_week": 2, "decision_rule": "advance if assay CV <= 10%"}],
        "risks": [{"risk_id": "R1", "wp_ids": ["WP1"], "probability": "medium", "impact": "high", "trigger": "CV > 10%", "mitigation": "pilot calibration", "contingency": "repeat with reference material", "owner": "PI"}],
    },
    "patent_claim_map": {
        "features": [
            {"feature_id": "F1", "support_state": "explicit", "source_anchors": ["Figure 2"]},
            {"feature_id": "F2", "support_state": "explicit", "source_anchors": ["Experiment 4"]},
        ],
        "claims": [
            {"claim_id": "C1", "role": "independent", "parent_claim_ids": [], "feature_ids": ["F1"]},
            {"claim_id": "C2", "role": "dependent", "parent_claim_ids": ["C1"], "feature_ids": ["F1", "F2"]},
        ],
    },
    "prior_art": {
        "critical_date": "2025-01-01",
        "invention_source_ids": ["INV1"],
        "candidate_records": [
            {"record_id": "P1", "source_id": "S1", "publication_date": "2024-01-01", "declared_lane": "prior_art_candidate", "matched_feature_ids": ["F1"]},
            {"record_id": "P2", "source_id": "S2", "publication_date": "2025-02-01", "declared_lane": "post_cutoff_background", "matched_feature_ids": []},
        ],
    },
    "chemistry_conditions": {
        "records": [
            {"record_id": "R1", "entity_id": "InChIKey-X", "endpoint": "yield", "basis": "isolated", "conditions": {"temperature": {"value": 25, "unit": "C"}, "time": {"value": 1, "unit": "h"}, "pressure": {"value": 1, "unit": "atm"}, "solvent": "water", "catalyst": "Pd", "atmosphere": "N2"}},
            {"record_id": "R2", "entity_id": "InChIKey-X", "endpoint": "yield", "basis": "isolated", "conditions": {"temperature": {"value": 298.15, "unit": "K"}, "time": {"value": 60, "unit": "min"}, "pressure": {"value": 101.325, "unit": "kPa"}, "solvent": "Water", "catalyst": "pd", "atmosphere": "n2"}},
        ],
    },
    "integrity_case": {
        "triage_items": [
            {"item_id": "I1", "kind": "observed_fact", "text": "The archived image hash differs from the submitted image hash.", "source_ids": ["H1", "H2"]},
            {"item_id": "I2", "kind": "reported_statement", "text": "The analyst reported exporting the panel twice.", "source_ids": ["N1"], "attributed_to": "analyst note"},
        ],
        "signals": {"data_security_breach": True, "evidence_loss_risk": False, "regulated_research": False, "immediate_participant_danger": False, "immediate_environment_danger": False, "active_submission": False, "authorship_dispute": False, "citation_integrity": False},
        "declared_severity": "high",
        "declared_scope": ["provenance", "data_security", "privacy"],
        "proposed_actions": [
            {"action": "preserve_evidence"}, {"action": "restrict_access"}, {"action": "document_chronology"},
            {"action": "authorized_referral", "authority": "institutional research integrity office"},
        ],
    },
    "registry": {
        "registry": {
            "schema_version": "1.0", "study_id": "STUDY-L2", "analysis_contract_id": "AC-L2", "registry_version": "1",
            "results": [{"result_id": "RES-1", "analysis_id": "AN-1", "outcome_id": "OUT-1", "result_kind": "inferential", "analysis_population": "all units", "effect_measure": "mean difference", "estimate": -2.4, "unit": "AU", "direction": "lower_in_treatment", "ci": {"level": 0.95, "lower": -3.7, "upper": -1.1}, "p_value": 0.0012, "multiplicity_status": "single primary", "n": {"experimental_units": 48, "observations": 48}, "diagnostics": [], "sensitivity_analyses": [], "source_anchor": "analysis.json#/primary", "status": "verified"}],
            "uses": [{"use_id": "U1", "consumer_type": "figure", "anchor": "Fig. 2a", "result_id": "RES-1", "values": {"estimate": -2.4, "ci.lower": -3.7, "n.experimental_units": 48}}],
        },
        "analysis_contract": copy.deepcopy(BASES["zju-statistics-audit"]["contract"]),
    },
    "figure": {
        "rows": copy.deepcopy(BASES["zju-scientific-figure"]["rows"]),
        "tamper_data_after_registry": False,
        "spec": {
            "figure_id": "FIG-L2", "bounded_conclusion": "Response changes with dose in the measured range.",
            "result_id": "RES-1", "analysis_id": "AN-1", "plot_type": "scatter_regression",
            "x_column": "dose", "y_column": "response", "experimental_unit_column": "unit",
            "experimental_unit": "sample", "x_label": "Dose (AU)", "y_label": "Response (AU)",
            "formats": ["png", "svg", "pdf"], "width_mm": 89, "height_mm": 70, "dpi": 150
        }
    },
}


def mutation(op: str, path: str, value: Any = None) -> dict[str, Any]:
    row = {"op": op, "path": path}
    if op != "delete":
        row["value"] = value
    return row


def field_probe(
    *, executor: str, adapter: str, behavior: str, field: str,
    positive: Any, boundary: Any, negative: Any = None,
    boundary_accepts: bool = True, negative_op: str = "delete",
    negative_signal: str | None = None, base: str | None = None,
    cross: Any | None = None,
) -> dict[str, Any]:
    variants = {
        "positive": {"mutations": [mutation("set", field, positive)], "accepted": True},
        "boundary": {"mutations": [mutation("set", field, boundary)], "accepted": boundary_accepts},
        "negative": {"mutations": [mutation(negative_op, field, negative)], "accepted": False},
        "cross_handoff": {"mutations": [mutation("set", field, cross if cross is not None else positive)], "accepted": True},
    }
    if negative_signal:
        variants["negative"]["surface_contains"] = negative_signal
    return {
        "executor": executor, "adapter": adapter, "measured_behavior": behavior,
        "capability_input_fields": [field], "base": base, "variants": variants,
    }


def custom_probe(
    *, executor: str, adapter: str, behavior: str, fields: list[str],
    base: str | None, variants: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {"executor": executor, "adapter": adapter, "measured_behavior": behavior, "capability_input_fields": fields, "base": base, "variants": variants}


# A capability belongs here only when no deterministic executor/oracle exists.
# The protocol-v3 development suite currently has concrete oracles for all 100
# matrix capabilities; keep the fail-closed planning path for future additions.
BLOCKED_CAPABILITIES: dict[tuple[str, str], str] = {}


CAPABILITY_PROBES: dict[tuple[str, str], dict[str, Any]] = {}


def register(skill: str, capability: str, probe: dict[str, Any]) -> None:
    CAPABILITY_PROBES[(skill, capability)] = probe


# Literature discovery uses the saturation executor for query/coverage/stopping
# and the record normalizer for identity and handoff behavior.
register("zju-literature-search", "query_design", custom_probe(
    executor="skills/zju-literature-search/scripts/assess_search_saturation.py", adapter="saturation", base="saturation",
    behavior="Detect whether required concepts were encoded and return a gap-targeted next action when a concept is absent.", fields=["required_concepts", "evidence_gaps"],
    variants={
        "positive": {"mutations": [mutation("set", "required_concepts", ["material", "failure"])], "accepted": True, "detail_path": "saturation.stop_recommended", "detail_equals": True},
        "boundary": {"mutations": [mutation("set", "required_concepts", ["material", "failure", "durability"])], "accepted": True, "detail_path": "saturation.next_action", "detail_equals": "run_gap_targeted_query_for_missing_concepts"},
        "negative": {"mutations": [mutation("set", "required_concepts", [])], "accepted": True, "detail_path": "coverage.required_concepts", "detail_equals": []},
        "cross_handoff": {"mutations": [mutation("set", "evidence_gaps", [{"gap_id": "G-CROSS", "status": "open", "severity": "critical"}])], "accepted": True, "detail_path": "saturation.stop_recommended", "detail_equals": False},
    },
))
register("zju-literature-search", "multi_source_recall", custom_probe(
    executor="skills/zju-literature-search/scripts/assess_search_saturation.py", adapter="saturation", base="saturation",
    behavior="Measure required source-family coverage and expose a source-targeted next action when one family is absent.", fields=["required_source_families", "rounds.1.source_family"],
    variants={
        "positive": {"mutations": [mutation("set", "required_source_families", ["broad_index", "domain_database"])], "accepted": True, "detail_path": "coverage.missing_source_families", "detail_equals": []},
        "boundary": {"mutations": [mutation("set", "required_source_families", ["broad_index", "domain_database", "citation_index"])], "accepted": True, "detail_path": "coverage.missing_source_families", "detail_contains": "citation_index"},
        "negative": {"mutations": [mutation("set", "rounds.1.source_family", "broad_index")], "accepted": True, "detail_path": "coverage.missing_source_families", "detail_contains": "domain_database"},
        "cross_handoff": {"mutations": [mutation("set", "rounds.1.source_family", "domain_database")], "accepted": True, "detail_path": "coverage.covered_source_families", "detail_contains": "domain_database"},
    },
))
register("zju-literature-search", "identity_dedup", custom_probe(
    executor="skills/zju-literature-search/scripts/normalize_records.py", adapter="literature", base=None,
    behavior="Collapse DOI aliases while preserving an identifier-free record as a separate work.", fields=["records.0.doi", "records.1.DOI", "records.2.year", "records.0.query_id"],
    variants={
        "positive": {"mutations": [mutation("set", "records.1.DOI", "doi:10.1000/l2")], "accepted": True, "detail_path": "record_count", "detail_equals": 2},
        "boundary": {"mutations": [mutation("delete", "records.2.year")], "accepted": True, "detail_path": "identifier_missing_count", "detail_equals": 1},
        "negative": {"mutations": [mutation("set", "records", "not-an-array")], "accepted": False, "surface_contains": "get"},
        "cross_handoff": {"mutations": [mutation("set", "records.0.query_id", "QUERY-HANDOFF")], "accepted": True, "detail_path": "query_ids", "detail_contains": "QUERY-HANDOFF"},
    },
))
register("zju-literature-search", "screening_saturation", custom_probe(
    executor="skills/zju-literature-search/scripts/assess_search_saturation.py", adapter="saturation", base="saturation",
    behavior="Compute unique eligible yield and stop only when marginal-yield and unresolved-gap rules allow it.", fields=["rounds.2.eligible_ids", "evidence_gaps"],
    variants={
        "positive": {"mutations": [mutation("set", "rounds.2.eligible_ids", ["C"])], "accepted": True, "detail_path": "saturation.stop_recommended", "detail_equals": True},
        "boundary": {"mutations": [mutation("set", "rounds.2.record_ids", ["C", "D"]), mutation("set", "rounds.2.eligible_ids", ["C", "D"])], "accepted": True, "detail_path": "saturation.stop_recommended", "detail_equals": False},
        "negative": {"mutations": [mutation("set", "evidence_gaps", [{"gap_id": "G-NEG", "status": "open", "severity": "critical"}])], "accepted": True, "detail_path": "saturation.stop_recommended", "detail_equals": False},
        "cross_handoff": {"mutations": [mutation("set", "rounds.2.eligible_ids", [])], "accepted": True, "detail_path": "totals.unique_eligible", "detail_equals": 1},
    },
))
CAPABILITY_PROBES[("zju-literature-search", "screening_saturation")]["variantize_cross"] = False
register("zju-literature-search", "search_handoff", custom_probe(
    executor="skills/zju-literature-search/scripts/normalize_records.py", adapter="literature", base=None,
    behavior="Preserve source database, query ID, stable identity key, and missing-identifier disclosure in normalized handoff rows.", fields=["records.0.source", "records.0.query_id", "records.2.title"],
    variants={
        "positive": {"mutations": [mutation("set", "records.0.query_id", "Q-HANDOFF-1")], "accepted": True, "detail_path": "query_ids", "detail_contains": "Q-HANDOFF-1"},
        "boundary": {"mutations": [mutation("delete", "records.2.title")], "accepted": True, "detail_path": "identity_missing_count", "detail_equals": 1},
        "negative": {"mutations": [mutation("set", "records.0", "malformed")], "accepted": False, "surface_contains": "get"},
        "cross_handoff": {"mutations": [mutation("set", "records.0.source", "ZJU export")], "accepted": True, "detail_path": "source_databases", "detail_contains": "ZJU export"},
    },
))

# Full-text classification exposes all five target behaviors in its returned
# source package and explicit sensitive-input rejection.
register("zju-fulltext-access", "target_identity", custom_probe(executor=EXECUTORS["zju-fulltext-access"][0], adapter="fulltext", base=None, behavior="Normalize DOI and preserve requested work/version identity.", fields=["doi", "record_id"], variants={
    "positive": {"mutations": [mutation("set", "doi", "https://doi.org/10.1000/TARGET")], "accepted": True, "detail_path": "reader_handoff.record_id", "detail_equals": "REC-1"},
    "boundary": {"mutations": [mutation("set", "record_id", ""), mutation("set", "doi", "10.1000/target")], "accepted": True, "detail_path": "reader_handoff.record_id", "detail_equals": "doi:10.1000/target"},
    "negative": {"mutations": [mutation("set", "record_id", ""), mutation("set", "doi", "")], "accepted": True, "detail_path": "route", "detail_equals": "open_access"},
    "cross_handoff": {"mutations": [mutation("set", "record_id", "REC-CROSS"), mutation("set", "doi", "doi:10.1000/target")], "accepted": True, "detail_path": "reader_handoff.record_id", "detail_equals": "REC-CROSS"},
}))
register("zju-fulltext-access", "lawful_route", custom_probe(executor=EXECUTORS["zju-fulltext-access"][0], adapter="fulltext", base=None, behavior="Rank OA, repository, official API, licensed, and manual routes without claiming entitlement.", fields=["oa_url", "repository_url", "api_url", "database"], variants={
    "positive": {"mutations": [mutation("set", "oa_url", "https://example.org/oa.pdf")], "accepted": True, "detail_path": "route", "detail_equals": "open_access"},
    "boundary": {"mutations": [mutation("set", "oa_url", ""), mutation("set", "repository_url", "https://repository.example/paper.pdf")], "accepted": True, "detail_path": "route", "detail_equals": "repository_copy"},
    "negative": {"mutations": [mutation("set", "oa_url", ""), mutation("set", "doi", ""), mutation("set", "database", "")], "accepted": True, "detail_path": "route", "detail_equals": "manual_citation_resolution"},
    "cross_handoff": {"mutations": [mutation("set", "oa_url", ""), mutation("set", "api_url", "https://api.example/fulltext"), mutation("set", "api_access", "public")], "accepted": True, "detail_path": "route", "detail_equals": "official_public_api"},
}))
register("zju-fulltext-access", "source_package", custom_probe(executor=EXECUTORS["zju-fulltext-access"][0], adapter="fulltext", base=None, behavior="Account for requested full text, supplement, and data components.", fields=["requested_components"], variants={
    "positive": {"mutations": [mutation("set", "requested_components", ["full_text", "supplement", "data"])], "accepted": True, "detail_path": "source_package_complete", "detail_equals": True},
    "boundary": {"mutations": [mutation("set", "requested_components", ["full_text"])], "accepted": True, "detail_path": "source_package_complete", "detail_equals": True},
    "negative": {"mutations": [mutation("set", "requested_components", ["full_text", "missing_component"])], "accepted": True, "detail_path": "missing_components", "detail_contains": "missing_component"},
    "cross_handoff": {"mutations": [mutation("set", "requested_components", ["supplement"])], "accepted": True, "detail_path": "source_package_complete", "detail_equals": True},
}))
register("zju-fulltext-access", "failure_recovery", custom_probe(executor=EXECUTORS["zju-fulltext-access"][0], adapter="fulltext", base=None, behavior="Return an interactive or manual recovery route when direct OA is absent.", fields=["oa_url", "database", "doi"], variants={
    "positive": {"mutations": [mutation("set", "oa_url", ""), mutation("set", "database", "Scopus")], "accepted": True, "detail_path": "route", "detail_equals": "zju_library_interactive"},
    "boundary": {"mutations": [mutation("set", "oa_url", ""), mutation("set", "database", "")], "accepted": True, "detail_path": "route", "detail_equals": "manual_identifier_lookup"},
    "negative": {"mutations": [mutation("set", "oa_url", ""), mutation("set", "database", ""), mutation("set", "doi", "")], "accepted": True, "detail_path": "route", "detail_equals": "manual_citation_resolution"},
    "cross_handoff": {"mutations": [mutation("set", "oa_url", ""), mutation("set", "database", "ZJU Library")], "accepted": True, "detail_path": "requires_interactive_authentication", "detail_equals": True},
}))
register("zju-fulltext-access", "access_boundary", field_probe(executor=EXECUTORS["zju-fulltext-access"][0], adapter="fulltext", behavior="Reject credentials rather than classifying or persisting them.", field="password", positive=None, boundary="", negative="must-not-persist", negative_op="set", boundary_accepts=True, negative_signal="blocked_sensitive_input", cross=None))

# Metadata, status, and atom-level claim-support validators.
register("zju-reference-audit", "field_identity", field_probe(executor=EXECUTORS["zju-reference-audit"][0], adapter="reference", behavior="Compare canonical DOI identity after normalization.", field="submitted", positive="https://doi.org/10.1000/l2.", boundary="doi:10.1000/l2", negative="10.1000/wrong", negative_op="set", boundary_accepts=True, cross="10.1000/L2"))
register("zju-reference-audit", "status_version", field_probe(executor="skills/zju-reference-audit/scripts/validate_status_freshness.py", adapter="status_freshness", base="status", behavior="Reject stale or undated correction/retraction status evidence.", field="records.0.status_checked_at", positive="2026-08-14", boundary="2026-07-16", negative="2020-01-01", negative_op="set", boundary_accepts=True, negative_signal="stale", cross="2026-08-15"))
register("zju-reference-audit", "conflict_uncertainty", field_probe(executor=EXECUTORS["zju-reference-audit"][0], adapter="reference", behavior="Classify field conflict instead of silently accepting mismatched canonical metadata.", field="canonical", positive="10.1000/l2", boundary="https://doi.org/10.1000/L2", negative="10.1000/conflict", negative_op="set", boundary_accepts=True, cross="doi:10.1000/l2"))
register("zju-reference-audit", "claim_support", custom_probe(
    executor="skills/zju-reference-audit/scripts/audit_claim_support.py", adapter="claim_support", base="claim_support",
    behavior="Compute atom-level support from source-anchored evidence facts without conflating metadata identity.",
    fields=["claims.0.atoms.0.value", "claims.0.atoms.0.material", "evidence_facts.0.value"], variants={
        "positive": {"mutations": [mutation("set", "claims.0.atoms.0.value", "2.40")], "accepted": True, "detail_path": "claims.0.atoms.0.status", "detail_equals": "supported"},
        "boundary": {"mutations": [mutation("set", "claims.0.atoms.0.value", 2.5), mutation("set", "claims.0.atoms.0.declared_status", "contradicted"), mutation("set", "claims.0.atoms.0.material", False)], "accepted": True, "detail_path": "claims.0.atoms.0.status", "detail_equals": "contradicted"},
        "negative": {"mutations": [mutation("set", "claims.0.atoms.0.value", 2.5), mutation("set", "claims.0.atoms.0.declared_status", "contradicted")], "accepted": False, "surface_contains": "material claim atom is contradicted"},
        "cross_handoff": {"mutations": [mutation("set", "evidence_facts.0.value", "2.400"), mutation("set", "claims.0.atoms.0.value", 2.4)], "accepted": True, "detail_path": "oracle_id", "detail_equals": "claim_atom_support_v1"},
    },
))
CAPABILITY_PROBES[("zju-reference-audit", "claim_support")]["oracle_id"] = "claim_atom_support_v1"
CAPABILITY_PROBES[("zju-reference-audit", "claim_support")]["variantize_cross"] = False
register("zju-reference-audit", "repair_actions", custom_probe(executor=EXECUTORS["zju-reference-audit"][0], adapter="reference_audit", base=None, behavior="Return a field-level mismatch record and repair-oriented action for submitted versus canonical metadata.", fields=["submitted", "canonical"], variants={
    "positive": {"mutations": [mutation("set", "submitted", "10.1000/wrong")], "accepted": True, "detail_path": "field_differences.doi.status", "detail_equals": "material_mismatch"},
    "boundary": {"mutations": [mutation("set", "submitted", "doi:10.1000/l2")], "accepted": True, "detail_path": "status", "detail_equals": "verified"},
    "negative": {"mutations": [mutation("set", "canonical", "")], "accepted": False},
    "cross_handoff": {"mutations": [mutation("set", "submitted", "https://doi.org/10.1000/L2.")], "accepted": True, "detail_path": "status", "detail_equals": "verified"},
}))

CAPABILITY_PROBES[("zju-reference-audit", "field_identity")]["variants"]["cross_handoff"]["mutations"].append(
    mutation("set", "canonical", "10.1000/l2")
)
CAPABILITY_PROBES[("zju-reference-audit", "conflict_uncertainty")]["variants"]["cross_handoff"]["mutations"].append(
    mutation("set", "submitted", "10.1000/l2")
)
CAPABILITY_PROBES[("zju-reference-audit", "repair_actions")]["variants"]["cross_handoff"]["mutations"].append(
    mutation("set", "canonical", "10.1000/l2")
)

# Reader source extraction and saved-card contract validation.
register("zju-paper-reader", "coverage_anchors", custom_probe(executor=EXECUTORS["zju-paper-reader"][0], adapter="reader", base=None, behavior="Create page IDs and page anchors from a local source.", fields=["source_text", "source_type"], variants={
    "positive": {"mutations": [mutation("set", "source_text", "# Abstract\nText.\n# Results\nResult.\n")], "accepted": True, "detail_path": "pages.0.anchor", "detail_equals": "[p. 1]"},
    "boundary": {"mutations": [mutation("set", "source_text", "# Abstract\nShort.\n")], "accepted": True, "detail_path": "page_count", "detail_equals": 1},
    "negative": {"mutations": [mutation("set", "source_type", "fake_pdf"), mutation("set", "source_text", "not actually a PDF")], "accepted": False, "surface_contains": "PDF"},
    "cross_handoff": {"mutations": [mutation("set", "source_text", "# Methods\nFigure 1.\n")], "accepted": True, "detail_path": "pages.0.page_id", "detail_equals": "page-0001"},
}))
register("zju-paper-reader", "object_reading", custom_probe(executor=EXECUTORS["zju-paper-reader"][0], adapter="reader", base=None, behavior="Count figure, table, and equation mentions in the source bundle.", fields=["source_text", "source_type"], variants={
    "positive": {"mutations": [mutation("set", "source_text", "# Results\nFigure 2 and Table 1 report Equation (1).\n")], "accepted": True, "detail_path": "coverage.figure_mentions", "detail_equals": 1},
    "boundary": {"mutations": [mutation("set", "source_text", "# Results\nFigure 2 only.\n")], "accepted": True, "detail_path": "coverage.table_mentions", "detail_equals": 0},
    "negative": {"mutations": [mutation("set", "source_type", "fake_pdf"), mutation("set", "source_text", "not actually a PDF")], "accepted": False, "surface_contains": "PDF"},
    "cross_handoff": {"mutations": [mutation("set", "source_text", "# Results\nFig. 3 and Eq. (2).\n")], "accepted": True, "detail_path": "coverage.equation_mentions", "detail_equals": 1},
}))
for capability, heading, behavior in (
    ("argument_spine", "# Argument spine", "Require the saved Paper Card argument-spine section."),
    ("claim_separation", "# Claim-evidence map", "Require an anchored claim-evidence map and claim row."),
    ("reader_handoff", "# Evidence-synthesis handoff", "Require the evidence-synthesis handoff section."),
):
    register("zju-paper-reader", capability, field_probe(executor="skills/zju-paper-reader/scripts/validate_reader.py", adapter="reader_contract", base="reader_card", behavior=behavior, field="content", positive=READER_CARD_BASE["content"] + f"\n{heading}\n", boundary=READER_CARD_BASE["content"], negative=READER_CARD_BASE["content"].replace(heading, ""), negative_op="set", boundary_accepts=True if heading in READER_CARD_BASE["content"] else False, negative_signal=capability, cross=READER_CARD_BASE["content"] + "\n[p. 3, Discussion]\n"))

CAPABILITY_PROBES[("zju-paper-reader", "claim_separation")]["variants"]["negative"]["surface_contains"] = "claim_evidence_map"
CAPABILITY_PROBES[("zju-paper-reader", "reader_handoff")]["variants"]["negative"]["surface_contains"] = "synthesis_handoff"

# Experiment run-manifest contracts plus an append-only correction hash chain.
register("zju-experiment-log", "mixed_input_capture", field_probe(executor=EXECUTORS["zju-experiment-log"][0], adapter="valid", behavior="Require at least one traceable raw input artifact in a completed run.", field="inputs", positive=BASES["zju-experiment-log"]["inputs"], boundary=[{"artifact_id": "RAW-1", "role": "raw_data", "path": "raw/min.txt", "sha256": "d" * 64}], negative=[], negative_op="set", boundary_accepts=True, negative_signal="inputs", cross=BASES["zju-experiment-log"]["inputs"] + [{"artifact_id": "RAW-2", "role": "metadata", "path": "raw/image-metadata.json", "sha256": "e" * 64}]))
register("zju-experiment-log", "parseable_record", field_probe(executor=EXECUTORS["zju-experiment-log"][0], adapter="valid", behavior="Validate required run identity and timestamp fields.", field="started_at", positive="2026-08-10T08:00:00+08:00", boundary="2026-08-10T00:00:00Z", negative="", negative_op="set", boundary_accepts=True, negative_signal="started_at", cross="2026-08-11T08:00:00+08:00"))
register("zju-experiment-log", "design_handoff", field_probe(executor=EXECUTORS["zju-experiment-log"][0], adapter="valid", behavior="Require experimental and observational units in the design snapshot.", field="design_snapshot.experimental_unit", positive="sample", boundary="animal", negative="", negative_op="set", boundary_accepts=True, negative_signal="experimental_unit", cross="plot"))
register("zju-experiment-log", "lineage_hashes", field_probe(executor=EXECUTORS["zju-experiment-log"][0], adapter="valid", behavior="Reject output lineage that refers to an unknown raw artifact ID.", field="outputs.0.derived_from", positive=["RAW-1"], boundary=[], negative=["RAW-404"], negative_op="set", boundary_accepts=True, negative_signal="Unknown parent", cross=["RAW-1"]))
register("zju-experiment-log", "append_only_correction", custom_probe(
    executor="skills/zju-experiment-log/scripts/validate_append_only_log.py", adapter="append_log", base="append_log",
    behavior="Verify an append-only correction chain binds each patch to the exact earlier event bytes.", fields=["events"], variants={
        "positive": {"mutations": [mutation("set", "events", _APPEND_CORRECTION_EVENTS)], "accepted": True, "detail_path": "corrections", "detail_equals": 1},
        "boundary": {"mutations": [mutation("set", "events", _seal_log_events([{"event_id": "EV-B1", "event_type": "entry", "timestamp": "2026-08-15T10:00:00+08:00", "payload": {"observation": "boundary entry"}}]))], "accepted": True, "detail_path": "corrections", "detail_equals": 0},
        "negative": {"mutations": [mutation("set", "events", _APPEND_TAMPERED_EVENTS)], "accepted": False, "surface_contains": "event bytes do not match event_hash"},
        "cross_handoff": {"mutations": [mutation("set", "events", _APPEND_CROSS_EVENTS)], "accepted": True, "detail_path": "oracle_id", "detail_equals": "append_only_hash_chain_v1"},
    },
))
CAPABILITY_PROBES[("zju-experiment-log", "append_only_correction")]["oracle_id"] = "append_only_hash_chain_v1"
CAPABILITY_PROBES[("zju-experiment-log", "append_only_correction")]["variantize_cross"] = False

CAPABILITY_PROBES[("zju-experiment-log", "mixed_input_capture")]["variants"]["cross_handoff"]["mutations"].append(
    mutation("set", "outputs.0.derived_from", ["RAW-1"])
)
CAPABILITY_PROBES[("zju-experiment-log", "lineage_hashes")]["variants"]["cross_handoff"]["mutations"].extend([
    mutation("set", "inputs.0.artifact_id", "RAW-CROSS"),
    mutation("set", "outputs.0.derived_from", ["RAW-CROSS"]),
])

# Statistics dispatches to design contract, numeric execution, and canonical
# result reconciliation instead of treating one validator as all capabilities.
register("zju-statistics-audit", "design_estimand", field_probe(executor="skills/zju-statistics-audit/scripts/validate_analysis_contract.py", adapter="analysis_contract", behavior="Reject a missing estimand or experimental-unit declaration in a frozen analysis contract.", field="contract.analyses.0.estimand", positive="treatment minus control mean", boundary="bounded mean contrast", negative="", negative_op="set", boundary_accepts=True, negative_signal="estimand", cross="prespecified group mean difference"))
register("zju-statistics-audit", "method_execution", custom_probe(executor=EXECUTORS["zju-statistics-audit"][0], adapter="statistics", base=None, behavior="Execute a supported Welch test and reject an unsupported model rather than approximating it.", fields=["contract.analyses.0.execution.method"], variants={
    "positive": {"mutations": [mutation("set", "contract.analyses.0.execution.method", "welch_ttest")], "accepted": True},
    "boundary": {"mutations": [mutation("set", "contract.analyses.0.execution.method", "welch_ttest"), mutation("set", "contract.analyses.0.execution.confidence_level", 0.9)], "accepted": True},
    "negative": {"mutations": [mutation("set", "contract.analyses.0.execution.method", "unsupported_mixed_model")], "accepted": False, "surface_contains": "unsupported"},
    "cross_handoff": {"mutations": [mutation("set", "contract.analyses.0.execution.method", "welch_ttest"), mutation("set", "contract.analyses.0.execution.result_id", "RES-METHOD")], "accepted": True},
}))
register("zju-statistics-audit", "effect_uncertainty", field_probe(executor=EXECUTORS["zju-statistics-audit"][0], adapter="statistics", behavior="Emit estimate, confidence interval, p value, and unit counts from executable data.", field="contract.analyses.0.execution.confidence_level", positive=0.95, boundary=0.9, negative=1.5, negative_op="set", boundary_accepts=True, negative_signal="confidence", cross=0.99))
register("zju-statistics-audit", "multiplicity_missingness", field_probe(executor="skills/zju-statistics-audit/scripts/validate_analysis_contract.py", adapter="analysis_contract", behavior="Require a declared missing-data and multiplicity strategy before execution.", field="contract.analyses.0.missing_data_strategy", positive="complete case", boundary="prespecified complete-case analysis", negative="", negative_op="set", boundary_accepts=True, negative_signal="missing_data_strategy", cross="multiple imputation sensitivity"))
register("zju-statistics-audit", "result_reconciliation", custom_probe(executor="skills/zju-statistics-audit/scripts/reconcile_result_registry.py", adapter="registry_reconcile", base="registry", behavior="Reject a consumer value that disagrees with the canonical result registry.", fields=["registry.uses.0.values.estimate", "registry.uses.0.consumer_type"], variants={
    "positive": {"mutations": [mutation("set", "registry.uses.0.values.estimate", -2.4)], "accepted": True},
    "boundary": {"mutations": [mutation("set", "registry.uses.0.values.estimate", -2.4), mutation("set", "registry.uses.0.anchor", "Table S2")], "accepted": True},
    "negative": {"mutations": [mutation("set", "registry.uses.0.values.estimate", 2.4)], "accepted": False, "surface_contains": "estimate"},
    "cross_handoff": {"mutations": [mutation("set", "registry.uses.0.consumer_type", "review_response")], "accepted": True},
}))

# Writing ledgers, contribution/result contracts, and explicit mode fidelity.
register("zju-scientific-writing", "claim_evidence", field_probe(executor=EXECUTORS["zju-scientific-writing"][0], adapter="writing", behavior="Reject a supported literature claim without evidence IDs and source anchor.", field="claims.0.evidence_ids", positive=["E1"], boundary=["E-BOUNDARY"], negative=[], negative_op="set", boundary_accepts=True, negative_signal="evidence", cross=["E1", "E2"]))
register("zju-scientific-writing", "numeric_invariants", custom_probe(executor="skills/zju-scientific-writing/scripts/check_writing_contract.py", adapter="writing_contract", base="writing_contract", behavior="Reject result references that are absent from the declared canonical result ID set.", fields=["results_sections.0.result_ids", "known_result_ids"], variants={
    "positive": {"mutations": [mutation("set", "results_sections.0.result_ids", ["RES-1"])], "accepted": True},
    "boundary": {"mutations": [mutation("set", "known_result_ids", ["RES-1", "RES-2"])], "accepted": True},
    "negative": {"mutations": [mutation("set", "results_sections.0.result_ids", ["RES-404"])], "accepted": False, "surface_contains": "RES-404"},
    "cross_handoff": {"mutations": [mutation("set", "results_sections.0.result_ids", ["RES-1"])], "accepted": True},
}))
register("zju-scientific-writing", "argument_quality", field_probe(executor="skills/zju-scientific-writing/scripts/check_writing_contract.py", adapter="writing_contract", base="writing_contract", behavior="Require a contribution need and bounded claim before declaring a writing package ready.", field="contributions.0.claim_boundary", positive="Tested system only", boundary="Limited to observed conditions", negative="", negative_op="set", boundary_accepts=True, negative_signal="claim_boundary", cross="No population extrapolation"))
register("zju-scientific-writing", "cross_artifact_consistency", custom_probe(executor="skills/zju-scientific-writing/scripts/check_writing_contract.py", adapter="writing_contract", base="writing_contract", behavior="Reject result IDs in a Results section that do not exist in the canonical handoff set.", fields=["results_sections.0.result_ids", "known_result_ids"], variants={
    "positive": {"mutations": [mutation("set", "results_sections.0.result_ids", ["RES-1"])], "accepted": True},
    "boundary": {"mutations": [mutation("set", "known_result_ids", ["RES-1", "RES-2"]), mutation("set", "results_sections.0.result_ids", ["RES-1"])], "accepted": True},
    "negative": {"mutations": [mutation("set", "results_sections.0.result_ids", ["UNKNOWN"])], "accepted": False, "surface_contains": "UNKNOWN"},
    "cross_handoff": {"mutations": [mutation("set", "results_sections.0.result_ids", ["RES-1"]), mutation("set", "results_sections.0.section_id", "R-CROSS")], "accepted": True},
}))
register("zju-scientific-writing", "mode_fidelity", custom_probe(
    executor="skills/zju-scientific-writing/scripts/validate_mode_fidelity.py", adapter="writing_mode", base="writing_mode",
    behavior="Enforce draft evidence links and preserve factual invariants exactly in polish mode.",
    fields=["mode", "declared_mode", "source_assertions", "output_assertions", "evidence_ledger"], variants={
        "positive": {
            "mutations": [mutation("set", "source_assertions.0.predicate", "decreased"), mutation("set", "output_assertions.0.predicate", "decreased")],
            "accepted": True, "detail_path": "preserved_assertions", "detail_equals": 1,
        },
        "boundary": {
            "mutations": [
                mutation("set", "mode", "draft"), mutation("set", "declared_mode", "draft"),
                mutation("set", "output_assertions", [{"assertion_id": "A1", "subject": "treatment", "predicate": "reduced", "object": "signal", "value": 2.4, "unit": "AU", "direction": "decrease", "evidence_ids": ["E1"]}]),
                mutation("set", "evidence_ledger", [{"evidence_id": "E1", "status": "verified"}]),
                mutation("set", "required_assertion_ids", ["A1"]),
            ],
            "accepted": True, "detail_path": "mode", "detail_equals": "draft",
        },
        "negative": {
            "mutations": [mutation("set", "output_assertions.0.value", 9.9)],
            "accepted": False, "surface_contains": "changed a factual invariant",
        },
        "cross_handoff": {
            "mutations": [mutation("set", "source_assertions.0.subject", "cross-cohort"), mutation("set", "output_assertions.0.subject", "cross-cohort")],
            "accepted": True, "detail_path": "preserved_assertions", "detail_equals": 1,
        },
    },
))
CAPABILITY_PROBES[("zju-scientific-writing", "mode_fidelity")].update({"oracle_id": "writing_mode_contract_v1", "variantize_cross": False})

CAPABILITY_PROBES[("zju-scientific-writing", "numeric_invariants")]["variants"]["cross_handoff"]["mutations"].append(
    mutation("set", "known_result_ids", ["RES-1"])
)
CAPABILITY_PROBES[("zju-scientific-writing", "cross_artifact_consistency")]["variants"]["cross_handoff"]["mutations"].append(
    mutation("set", "known_result_ids", ["RES-1"])
)

# Remaining validators expose capability-specific fields; each negative case
# mutates the field named by the capability and must fail closed.
register("zju-literature-monitor", "profile_versioning", custom_probe(executor=EXECUTORS["zju-literature-monitor"][0], adapter="monitor", base=None, behavior="Preserve the monitoring profile identity across a deterministic state update.", fields=["state.profile_id"], variants={
    "positive": {"mutations": [mutation("set", "state.profile_id", "MON-1")], "accepted": True, "detail_path": "state.profile_id", "detail_equals": "MON-1"},
    "boundary": {"mutations": [mutation("set", "state.profile_id", "MON-BOUNDARY")], "accepted": True, "detail_path": "state.profile_id", "detail_equals": "MON-BOUNDARY"},
    "negative": {"mutations": [mutation("set", "state.profile_id", "")], "accepted": True, "detail_path": "state.profile_id", "detail_equals": ""},
    "cross_handoff": {"mutations": [mutation("set", "state.profile_id", "MON-2")], "accepted": True, "detail_path": "state.profile_id", "detail_equals": "MON-2"},
}))
register("zju-literature-monitor", "state_transition", custom_probe(executor=EXECUTORS["zju-literature-monitor"][0], adapter="monitor", base=None, behavior="Classify a DOI record as new and retain its semantic version state.", fields=["records.0.version_status"], variants={
    "positive": {"mutations": [mutation("set", "records.0.version_status", "version_of_record")], "accepted": True, "detail_path": "state.seen.0.current_state.version_status", "detail_equals": "version_of_record"},
    "boundary": {"mutations": [mutation("set", "records.0.version_status", "preprint")], "accepted": True, "detail_path": "state.seen.0.current_state.version_status", "detail_equals": "preprint"},
    "negative": {"mutations": [mutation("set", "records.0.version_status", "")], "accepted": True, "detail_path": "state.seen.0.current_state", "detail_equals": {}},
    "cross_handoff": {"mutations": [mutation("set", "records.0.version_status", "corrected")], "accepted": True, "detail_path": "state.seen.0.current_state.version_status", "detail_equals": "corrected"},
}))
register("zju-literature-monitor", "run_accounting", field_probe(executor=EXECUTORS["zju-literature-monitor"][0], adapter="monitor", behavior="Return source-run transition counts without inventing zero-success records.", field="records", positive=BASES["zju-literature-monitor"]["records"], boundary=[], negative="not-an-array", negative_op="set", boundary_accepts=False, negative_signal="list", cross=BASES["zju-literature-monitor"]["records"] + [{"doi": "10.1000/new2", "title": "Second work"}]))
CAPABILITY_PROBES[("zju-literature-monitor", "run_accounting")]["variants"]["negative"]["surface_contains"] = "get"
register("zju-literature-monitor", "decision_priority", custom_probe(executor=EXECUTORS["zju-literature-monitor"][0], adapter="monitor", base=None, behavior="Escalate retraction and expression-of-concern records through the handoff queue.", fields=["records.0.retraction_status"], variants={
    "positive": {"mutations": [mutation("set", "records.0.retraction_status", "retracted")], "accepted": True, "detail_path": "handoff_queue.0.urgency", "detail_equals": "critical"},
    "boundary": {"mutations": [mutation("set", "records.0.retraction_status", "expression_of_concern")], "accepted": True, "detail_path": "handoff_queue.0.urgency", "detail_equals": "critical"},
    "negative": {"mutations": [mutation("set", "records.0.retraction_status", "")], "accepted": True, "detail_path": "handoff_queue.0.urgency", "detail_equals": "routine"},
    "cross_handoff": {"mutations": [mutation("set", "records.0.retraction_status", "retracted")], "accepted": True, "detail_path": "handoff_queue.0.next_skills", "detail_contains": "zju-evidence-synthesis"},
}))
register("zju-literature-monitor", "bounded_delivery", custom_probe(executor=EXECUTORS["zju-literature-monitor"][0], adapter="monitor", base=None, behavior="Reject monitoring state that contains a credential-like key.", fields=["state.token", "state.delivery_mode"], variants={
    "positive": {"mutations": [mutation("set", "state.delivery_mode", "bounded_local_archive")], "accepted": True},
    "boundary": {"mutations": [mutation("set", "state.delivery_mode", "archive_only")], "accepted": True},
    "negative": {"mutations": [mutation("set", "state.token", "secret")], "accepted": False, "surface_contains": "sensitive"},
    "cross_handoff": {"mutations": [mutation("set", "state.delivery_mode", "manual_notification")], "accepted": True},
}))
CAPABILITY_PROBES[("zju-literature-monitor", "bounded_delivery")]["variants"]["negative"]["surface_contains"] = "Sensitive"

register("zju-evidence-synthesis", "study_outcome_table", field_probe(executor=EXECUTORS["zju-evidence-synthesis"][0], adapter="valid", behavior="Require study, outcome, time point, effect, uncertainty, and source anchor in each evidence row.", field="rows.0.time_point", positive="week 4", boundary="endpoint", negative="", negative_op="set", boundary_accepts=True, negative_signal="time_point", cross="day 28"))
register("zju-evidence-synthesis", "bias_applicability", field_probe(executor=EXECUTORS["zju-evidence-synthesis"][0], adapter="valid", behavior="Require an explicit risk-of-bias assessment rather than silently assuming low risk.", field="rows.0.risk_of_bias", positive="low", boundary="not_assessable", negative="", negative_op="set", boundary_accepts=True, negative_signal="risk_of_bias", cross="high"))
register("zju-evidence-synthesis", "pooling_conflict", custom_probe(executor="skills/zju-evidence-synthesis/scripts/build_conflict_matrix.py", adapter="conflict_matrix", base="evidence_bundle", behavior="Detect opposing evidence roles within a claim group and report a directional conflict.", fields=["rows.1.evidence_role"], variants={
    "positive": {"mutations": [mutation("set", "rows.1.evidence_role", "contradicts")], "accepted": True, "detail_path": "summary.directional_conflicts", "detail_equals": 1},
    "boundary": {"mutations": [mutation("set", "rows.1.evidence_role", "contextual")], "accepted": True, "detail_path": "summary.directional_conflicts", "detail_equals": 0},
    "negative": {"mutations": [mutation("set", "rows.1.evidence_role", "unclear"), mutation("set", "rows.1.result_direction", "unclear")], "accepted": True, "detail_path": "summary.directional_conflicts", "detail_equals": 0},
    "cross_handoff": {"mutations": [mutation("set", "rows.1.evidence_role", "supports")], "accepted": True, "detail_path": "summary.directional_conflicts", "detail_equals": 0},
}))
register("zju-evidence-synthesis", "certainty_handoff", field_probe(executor=EXECUTORS["zju-evidence-synthesis"][0], adapter="valid", base="evidence_bundle", behavior="Require a certainty label and linked study IDs in the claim handoff.", field="claims.0.certainty", positive="low", boundary="very_low", negative="", negative_op="set", boundary_accepts=True, negative_signal="certainty", cross="moderate"))
register("zju-evidence-synthesis", "protocol_selection", custom_probe(
    executor="skills/zju-evidence-synthesis/scripts/select_protocol.py", adapter="protocol_select", base="protocol_selection",
    behavior="Select and verify a synthesis protocol from objective, search scope, deadline, and pooling compatibility.",
    fields=["objective", "comprehensive_search_required", "deadline_days", "compatible_effect_estimates_available", "declared_protocol"], variants={
        "positive": {
            "mutations": [mutation("set", "objective", "quantitative_pooling"), mutation("set", "compatible_effect_estimates_available", True), mutation("set", "declared_protocol", "meta_analysis")],
            "accepted": True, "detail_path": "selected_protocol", "detail_equals": "meta_analysis",
        },
        "boundary": {
            "mutations": [mutation("set", "objective", "evidence_mapping"), mutation("set", "comprehensive_search_required", False), mutation("set", "declared_protocol", "scoping_review")],
            "accepted": True, "detail_path": "selected_protocol", "detail_equals": "scoping_review",
        },
        "negative": {
            "mutations": [mutation("set", "objective", "quantitative_pooling"), mutation("set", "compatible_effect_estimates_available", False), mutation("set", "declared_protocol", "meta_analysis")],
            "accepted": False, "surface_contains": "compatible effect estimates",
        },
        "cross_handoff": {
            "mutations": [mutation("set", "objective", "time_bound_decision"), mutation("set", "comprehensive_search_required", False), mutation("set", "deadline_days", 21), mutation("set", "declared_protocol", "rapid_review")],
            "accepted": True, "detail_path": "selected_protocol", "detail_equals": "rapid_review",
        },
    },
))
CAPABILITY_PROBES[("zju-evidence-synthesis", "protocol_selection")].update({"oracle_id": "synthesis_protocol_decision_table_v1", "variantize_cross": False})

register("zju-hypothesis-design", "evidence_assumption", field_probe(executor=EXECUTORS["zju-hypothesis-design"][0], adapter="valid", behavior="Require evidence IDs and explicit assumptions for each competing hypothesis.", field="hypotheses.0.assumptions", positive=["A"], boundary=["Assumption uncertain"], negative=[], negative_op="set", boundary_accepts=True, negative_signal="assumptions", cross=["A", "B"]))
register("zju-hypothesis-design", "competing_mechanisms", custom_probe(executor=EXECUTORS["zju-hypothesis-design"][0], adapter="valid", base=None, behavior="Require at least two materially linked alternative hypotheses.", fields=["hypotheses"], variants={
    "positive": {"mutations": [mutation("set", "hypotheses", BASES["zju-hypothesis-design"]["hypotheses"])], "accepted": True},
    "boundary": {"mutations": [mutation("set", "hypotheses.1.mechanism", "M2 boundary alternative")], "accepted": True},
    "negative": {"mutations": [mutation("set", "hypotheses", BASES["zju-hypothesis-design"]["hypotheses"][:1])], "accepted": False, "surface_contains": "at least"},
    "cross_handoff": {"mutations": [mutation("set", "hypotheses.1.mechanism", "M2 cross-handoff alternative")], "accepted": True},
}))
register("zju-hypothesis-design", "predictions_falsifiers", field_probe(executor=EXECUTORS["zju-hypothesis-design"][0], adapter="valid", behavior="Reject a hypothesis with no falsifier.", field="hypotheses.0.falsifiers", positive=["F1"], boundary=["No replicated effect"], negative=[], negative_op="set", boundary_accepts=True, negative_signal="falsifier", cross=["Opposite direction", "Null result"] ))
register("zju-hypothesis-design", "causal_design", field_probe(executor=EXECUTORS["zju-hypothesis-design"][0], adapter="valid", behavior="Require an experimental unit and control in a discriminating experiment.", field="experiments.0.control", positive="vehicle", boundary="sham", negative="", negative_op="set", boundary_accepts=True, negative_signal="control", cross="untreated"))
register("zju-hypothesis-design", "discrimination_rules", field_probe(executor=EXECUTORS["zju-hypothesis-design"][0], adapter="valid", behavior="Require a pre-result decision rule for the competing-hypothesis experiment.", field="experiments.0.decision_rule", positive="predefined contrast", boundary="direction and precision threshold", negative="", negative_op="set", boundary_accepts=True, negative_signal="decision_rule", cross="stop if interval excludes material effect"))

# Figure probes all run the real renderer; the adapter changes the actual data
# or specification according to the capability field rather than adding labels.
for capability, behavior, field in (
    ("claim_data_binding", "Reject data bytes that do not match the canonical result registry.", "tamper_data_after_registry"),
    ("visual_encoding", "Bind scatter-regression marks to the declared x/y and experimental-unit columns.", "spec.y_column"),
    ("transformation_provenance", "Record source and specification hashes in the generated figure manifest.", "spec.bounded_conclusion"),
    ("reproducible_export", "Materialize PNG, SVG, and PDF exports with content hashes.", "spec.formats"),
    ("visual_qa", "Generate a manifest that can be reopened and verified for export completeness.", "spec.dpi"),
):
    if capability == "claim_data_binding":
        variants = {
            "positive": {"mutations": [mutation("set", field, False)], "accepted": True},
            "boundary": {"mutations": [mutation("set", field, False), mutation("set", "rows.0.response", 3.0001)], "accepted": True},
            "negative": {"mutations": [mutation("set", field, True)], "accepted": False, "surface_contains": "does not match"},
            "cross_handoff": {"mutations": [mutation("set", field, False), mutation("set", "spec.figure_id", "FIG-CROSS")], "accepted": True},
        }
    elif capability == "visual_encoding":
        variants = {
            "positive": {"mutations": [mutation("set", field, "response")], "accepted": True},
            "boundary": {"mutations": [mutation("set", "spec.x_label", "Dose")], "accepted": True},
            "negative": {"mutations": [mutation("set", field, "missing_column")], "accepted": False, "surface_contains": "missing_column"},
            "cross_handoff": {"mutations": [mutation("set", field, "response")], "accepted": True},
        }
    elif capability == "reproducible_export":
        variants = {
            "positive": {"mutations": [mutation("set", field, ["png", "svg", "pdf"])], "accepted": True, "detail_path": "export_formats", "detail_equals": ["png", "svg", "pdf"]},
            "boundary": {"mutations": [mutation("set", field, ["svg"])], "accepted": True, "detail_path": "export_formats", "detail_equals": ["svg"]},
            "negative": {"mutations": [mutation("set", field, ["unknown"])], "accepted": False, "surface_contains": "format"},
            "cross_handoff": {"mutations": [mutation("set", field, ["png", "pdf"])], "accepted": True},
        }
    elif capability == "visual_qa":
        variants = {
            "positive": {"mutations": [mutation("set", field, 150)], "accepted": True, "detail_path": "exports_content_hashed", "detail_equals": True},
            "boundary": {"mutations": [mutation("set", field, 72)], "accepted": True},
            "negative": {"mutations": [mutation("set", field, 0)], "accepted": False},
            "cross_handoff": {"mutations": [mutation("set", field, 300)], "accepted": True},
        }
    else:
        variants = {
            "positive": {"mutations": [mutation("set", field, "Response changes with dose in the measured range.")], "accepted": True, "detail_path": "manifest_has_provenance", "detail_equals": True},
            "boundary": {"mutations": [mutation("set", field, "Bounded result.")], "accepted": True},
            "negative": {"mutations": [mutation("set", field, "")], "accepted": False},
            "cross_handoff": {"mutations": [mutation("set", field, "Result RES-1 is bounded to this dataset.")], "accepted": True},
        }
    register("zju-scientific-figure", capability, custom_probe(executor=EXECUTORS["zju-scientific-figure"][0], adapter="figure", base="figure", behavior=behavior, fields=[field], variants=variants))

CAPABILITY_PROBES[("zju-scientific-figure", "visual_encoding")]["capability_input_fields"].append("spec.x_label")

for _figure_capability in ("claim_data_binding", "visual_encoding", "transformation_provenance", "reproducible_export", "visual_qa"):
    CAPABILITY_PROBES[("zju-scientific-figure", _figure_capability)]["variants"]["cross_handoff"]["mutations"].extend([
        mutation("set", "spec.result_id", "RES-1"),
        mutation("set", "spec.analysis_id", "AN-1"),
    ])

# Simple validator field probes for communication, review, sharing, proposal,
# patent, chemistry, integrity, and director contracts.
for capability, field, positive, boundary, negative in (
    ("evidence_arc", "slides.0.claim", "The paper tests X", "Bounded evidence arc", ""),
    ("slide_source_map", "slides.0.source_anchors", ["p.1"], ["Fig. 1"], []),
    ("terminology_notes", "slides.0.speaker_notes", "Introduce the question", "Brief note", ""),
):
    register("zju-paper2ppt", capability, field_probe(executor=EXECUTORS["zju-paper2ppt"][0], adapter="valid", behavior=f"Validate presentation {capability.replace('_', ' ')} fields before materialization.", field=field, positive=positive, boundary=boundary, negative=negative, negative_op="set", boundary_accepts=True, negative_signal=field.split(".")[-1], cross=positive))
for capability in ("pptx_artifact", "render_qa"):
    field = "slides.0.source_anchors" if capability == "pptx_artifact" else "slides.0.speaker_notes"
    register("zju-paper2ppt", capability, custom_probe(executor="skills/zju-paper2ppt/scripts/build_presentation.py", adapter="deck_artifact", base=None, behavior="Build and reopen a real OOXML PPTX with source anchors and notes." if capability == "pptx_artifact" else "Reopen every generated slide/notes part and reject missing provenance text.", fields=[field], variants={
        "positive": {"mutations": [mutation("set", "project_id", "D1-ARTIFACT" if capability == "pptx_artifact" else "D1-QA"), mutation("set", field, ["p.1"] if capability == "pptx_artifact" else "Explain source p.1")], "accepted": True},
        "boundary": {"mutations": [mutation("set", "project_id", "D1-ARTIFACT-B" if capability == "pptx_artifact" else "D1-QA-B"), mutation("set", field, ["Table 1"] if capability == "pptx_artifact" else "Short note")], "accepted": True},
        "negative": {"mutations": [mutation("set", "project_id", "D1-ARTIFACT-N" if capability == "pptx_artifact" else "D1-QA-N"), mutation("set", field, [] if capability == "pptx_artifact" else "")], "accepted": False},
        "cross_handoff": {"mutations": [mutation("set", "project_id", "D1-ARTIFACT-X" if capability == "pptx_artifact" else "D1-QA-X"), mutation("set", field, ["Fig. 2"] if capability == "pptx_artifact" else "Explain Fig. 2")], "accepted": True},
    }))

review_fields = {
    "claim_reconstruction": ("concerns.0.claim_pointer", "Results 1", "Abstract claim", ""),
    "validity_review": ("concerns.0.evidence_pointer", "Fig. 1", "Table 1", ""),
    "actionable_concerns": ("concerns.0.resolution_test", "Add or justify control", "Bound the claim", ""),
    "severity_fairness": ("concerns.0.severity", "major", "minor", "invalid"),
    "panel_integrity": ("mode", "single_review", "single_review", "panel_review"),
}
for cap, (field, pos, bound, neg) in review_fields.items():
    negative_accepts = False
    probe = field_probe(executor=EXECUTORS["zju-reviewer"][0], adapter="valid", behavior=f"Validate reviewer {cap.replace('_', ' ')} as a traceable report contract.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=True, negative_signal=field.split(".")[-1], cross=pos)
    if cap == "panel_integrity":
        probe["variants"]["boundary"]["mutations"].append(mutation("set", "assessment_boundary", "Abstract only"))
        probe["variants"]["negative"]["mutations"] += [mutation("set", "mutually_blind", True), mutation("delete", "immutable_packet_sha256")]
        probe["variants"]["negative"]["surface_contains"] = "immutable_packet_sha256"
    if cap == "severity_fairness":
        probe["variants"]["boundary"]["mutations"].append(mutation("set", "concerns.0.blocking", False))
    register("zju-reviewer", cap, probe)

response_fields = {
    "comment_ledger": ("items.0.verbatim_comment", "Add control", "Clarify control", ""),
    "action_truth": ("items.0.status", "verified_complete", "planned", "completed"),
    "evidence_diff": ("items.0.location", "Methods, paragraph 2", "Results, paragraph 1", "LOCATION_PENDING"),
    "scientific_disagreement": ("items.0.response_text", "We respectfully clarify the bounded evidence.", "We disagree and bound the claim.", ""),
    "package_consistency": ("items.0.manuscript_change", "Added control definition", "Updated limitation", ""),
}
for cap, (field, pos, bound, neg) in response_fields.items():
    register("zju-review-response", cap, field_probe(executor=EXECUTORS["zju-review-response"][0], adapter="valid", behavior=f"Validate response {cap.replace('_', ' ')} before package assembly.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=(cap != "action_truth"), negative_signal=field.split(".")[-1], cross=pos))

data_fields = {
    "artifact_inventory": ("artifacts.0.artifact_id", "D1", "D-BOUNDARY", ""),
    "access_restrictions": ("artifacts.0.access_route", "public_repository", "controlled_access", ""),
    "identifier_license": ("artifacts.0.identifier", "doi:10.1000/data", "accession:ABC123", ""),
    "reproducibility_package": ("artifacts.0.description", "source data and code", "source data", ""),
    "statement_consistency": ("artifacts.0.status", "ready", "restricted", "unknown"),
}
for cap, (field, pos, bound, neg) in data_fields.items():
    register("zju-data-availability", cap, field_probe(executor=EXECUTORS["zju-data-availability"][0], adapter="valid", behavior=f"Validate data-availability {cap.replace('_', ' ')} at artifact level.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=True, negative_signal=field.split(".")[-1], cross=pos))

CAPABILITY_PROBES[("zju-data-availability", "access_restrictions")]["variants"]["boundary"]["mutations"].append(
    mutation("set", "artifacts.0.restriction_basis", "participant consent and data-use agreement")
)
CAPABILITY_PROBES[("zju-data-availability", "artifact_inventory")]["variants"]["cross_handoff"]["mutations"] = [
    mutation("set", "artifacts.0.artifact_id", "D-CROSS")
]
register("zju-data-availability", "statement_consistency", custom_probe(
    executor="skills/zju-data-availability/scripts/validate_statement_consistency.py", adapter="data_statement", base="data_statement",
    behavior="Require a one-to-one, access-consistent mapping between claim-supporting artifacts and the availability statement.",
    fields=["inventory", "statement_entries"], variants={
        "positive": {
            "mutations": [mutation("set", "inventory.0.persistent_identifier", "doi:10.1000/data-v2"), mutation("set", "statement_entries.0.persistent_identifier", "doi:10.1000/data-v2")],
            "accepted": True, "detail_path": "statement_artifacts", "detail_equals": 1,
        },
        "boundary": {
            "mutations": [
                mutation("set", "inventory", [{"artifact_id": "D1", "supports_claims": True, "access": "restricted", "restriction_reason": "participant consent", "request_route": "data access committee", "decision_authority": "ethics-approved DAC"}]),
                mutation("set", "statement_entries", [{"artifact_id": "D1", "access": "restricted", "restriction_reason": "participant consent", "request_route": "data access committee", "decision_authority": "ethics-approved DAC"}]),
            ],
            "accepted": True, "detail_path": "omitted_required", "detail_equals": [],
        },
        "negative": {
            "mutations": [mutation("set", "statement_entries.0.access", "restricted")],
            "accepted": False, "surface_contains": "access state differs",
        },
        "cross_handoff": {
            "mutations": [
                mutation("set", "inventory", [{"artifact_id": "D-X", "supports_claims": True, "access": "embargoed", "embargo_until": "2027-01-31"}]),
                mutation("set", "statement_entries", [{"artifact_id": "D-X", "access": "embargoed", "embargo_until": "2027-01-31"}]),
            ],
            "accepted": True, "detail_path": "inventory_artifacts", "detail_equals": 1,
        },
    },
))
CAPABILITY_PROBES[("zju-data-availability", "statement_consistency")].update({"oracle_id": "data_statement_inventory_bijection_v1", "variantize_cross": False})

proposal_fields = {
    "call_compliance": ("scheme_status", "official_verified", "official_verified", "template_pending"),
    "research_canon": ("evidence.0.evidence_id", "E1", "E-BOUNDARY", ""),
    "gap_objectives": ("objectives.0.question", "Does X affect Y?", "Which mechanism affects Y?", ""),
    "work_packages": ("work_packages.0.decision_gate", "quality threshold", "precision threshold", ""),
    "feasibility_risk": ("risks.0.risk_id", "R1", "R-BOUNDARY", ""),
}
for cap, (field, pos, bound, neg) in proposal_fields.items():
    register("zju-proposal-writer", cap, field_probe(executor=EXECUTORS["zju-proposal-writer"][0], adapter="valid", behavior=f"Validate proposal {cap.replace('_', ' ')} links and readiness fields.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=True, negative_signal=field.split(".")[-1], cross=pos))

# The call-status enum is intentionally identical at the positive and boundary
# points; distinguish the boundary packet with a second real proposal identity.
CAPABILITY_PROBES[("zju-proposal-writer", "call_compliance")]["variants"]["boundary"]["mutations"].append(
    mutation("set", "proposal_id", "P-CALL-BOUNDARY")
)
CAPABILITY_PROBES[("zju-proposal-writer", "research_canon")]["variants"]["boundary"]["mutations"].append(
    mutation("set", "objectives.0.evidence_ids", ["E-BOUNDARY"])
)
CAPABILITY_PROBES[("zju-proposal-writer", "research_canon")]["variants"]["cross_handoff"]["mutations"].append(
    mutation("set", "objectives.0.evidence_ids", ["E1"])
)
register("zju-proposal-writer", "feasibility_risk", custom_probe(
    executor="skills/zju-proposal-writer/scripts/audit_feasibility.py", adapter="feasibility", base="proposal_feasibility",
    behavior="Audit dependency order, resource capacity, milestone timing, and critical-work-package risk coverage together.",
    fields=["project_weeks", "resource_capacities", "work_packages", "milestones", "risks"], variants={
        "positive": {
            "mutations": [mutation("set", "resource_capacities.researcher", 1.25)],
            "accepted": True, "detail_path": "resource_overloads", "detail_equals": [],
        },
        "boundary": {
            "mutations": [mutation("set", "work_packages.1.start_week", 6), mutation("set", "work_packages.1.end_week", 8)],
            "accepted": True, "detail_path": "topological_order", "detail_equals": ["WP1", "WP2"],
        },
        "negative": {
            "mutations": [mutation("set", "resource_capacities.researcher", 0.5)],
            "accepted": False, "surface_contains": "overloaded",
        },
        "cross_handoff": {
            "mutations": [mutation("set", "project_weeks", 10), mutation("set", "work_packages.1.end_week", 7), mutation("set", "risks.0.trigger", "cross-site CV > 10%")],
            "accepted": True, "detail_path": "risk_coverage", "detail_contains": "WP1",
        },
    },
))
CAPABILITY_PROBES[("zju-proposal-writer", "feasibility_risk")].update({"oracle_id": "proposal_schedule_resource_risk_v1", "variantize_cross": False})

patent_fields = {
    "confidentiality_disclosure": ("features.0.confidentiality", "unpublished", "public", ""),
    "feature_support": ("features.0.support_state", "explicit", "implicit", "unsupported"),
    "claim_maps": ("features.0.claim_role", "independent", "dependent", "invalid"),
    "prior_art_separation": ("sources.0.source_type", "paper", "experiment", ""),
    "inventorship_legal_boundary": ("features.0.source_anchor", "p. 4, lines 10-12", "Table 1", "SOURCE_REQUIRED"),
}
for cap, (field, pos, bound, neg) in patent_fields.items():
    register("zju-paper-to-patent", cap, field_probe(executor=EXECUTORS["zju-paper-to-patent"][0], adapter="valid", behavior=f"Validate patent handoff {cap.replace('_', ' ')} without legal conclusion.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=True, negative_signal=field.split(".")[-1], cross=pos))

CAPABILITY_PROBES[("zju-paper-to-patent", "feature_support")]["variants"]["boundary"]["mutations"] = [
    mutation("set", "features.0.support_state", "inherent")
]
register("zju-paper-to-patent", "claim_maps", custom_probe(
    executor="skills/zju-paper-to-patent/scripts/audit_claim_map.py", adapter="patent_claim_map", base="patent_claim_map",
    behavior="Validate supported feature coverage, independent/dependent claim roles, and an acyclic dependency map.",
    fields=["features", "claims"], variants={
        "positive": {
            "mutations": [mutation("set", "features.1.support_state", "inherent")],
            "accepted": True, "detail_path": "claim_order", "detail_equals": ["C1", "C2"],
        },
        "boundary": {
            "mutations": [mutation("set", "claims", [{"claim_id": "C1", "role": "independent", "parent_claim_ids": [], "feature_ids": ["F1"]}])],
            "accepted": True, "detail_path": "independent_claims", "detail_equals": 1,
        },
        "negative": {
            "mutations": [mutation("set", "claims.1.feature_ids", ["F1"])],
            "accepted": False, "surface_contains": "must add a limiting feature",
        },
        "cross_handoff": {
            "mutations": [
                mutation("set", "features", [
                    {"feature_id": "FX1", "support_state": "explicit", "source_anchors": ["Table S1"]},
                    {"feature_id": "FX2", "support_state": "inherent", "source_anchors": ["Method M2"]},
                    {"feature_id": "FX3", "support_state": "explicit", "source_anchors": ["Figure S3"]},
                ]),
                mutation("set", "claims", [
                    {"claim_id": "CX1", "role": "independent", "parent_claim_ids": [], "feature_ids": ["FX1"]},
                    {"claim_id": "CX2", "role": "dependent", "parent_claim_ids": ["CX1"], "feature_ids": ["FX1", "FX2"]},
                    {"claim_id": "CX3", "role": "dependent", "parent_claim_ids": ["CX2"], "feature_ids": ["FX1", "FX2", "FX3"]},
                ]),
            ],
            "accepted": True, "detail_path": "claim_order", "detail_equals": ["CX1", "CX2", "CX3"],
        },
    },
))
CAPABILITY_PROBES[("zju-paper-to-patent", "claim_maps")].update({"oracle_id": "patent_claim_dependency_support_v1", "variantize_cross": False})
register("zju-paper-to-patent", "prior_art_separation", custom_probe(
    executor="skills/zju-paper-to-patent/scripts/separate_prior_art.py", adapter="prior_art", base="prior_art",
    behavior="Keep invention evidence, pre-cutoff prior-art candidates, post-cutoff background, and unresolved dates in separate lanes without legal conclusions.",
    fields=["critical_date", "invention_source_ids", "candidate_records"], variants={
        "positive": {
            "mutations": [mutation("set", "candidate_records.0.matched_feature_ids", ["F1", "F2"])],
            "accepted": True, "detail_path": "lanes.prior_art_candidate", "detail_contains": "P1",
        },
        "boundary": {
            "mutations": [mutation("set", "candidate_records.1.publication_date", None), mutation("set", "candidate_records.1.declared_lane", "date_unresolved")],
            "accepted": True, "detail_path": "lanes.date_unresolved", "detail_contains": "P2",
        },
        "negative": {
            "mutations": [mutation("set", "candidate_records.0.source_id", "INV1")],
            "accepted": False, "surface_contains": "invention source cannot also be classified as prior art",
        },
        "cross_handoff": {
            "mutations": [
                mutation("set", "critical_date", "2026-01-01"),
                mutation("set", "candidate_records", [
                    {"record_id": "PX1", "source_id": "SX1", "publication_date": "2025-12-31", "declared_lane": "prior_art_candidate", "matched_feature_ids": ["FX1"]},
                    {"record_id": "PX2", "source_id": "SX2", "publication_date": "2026-01-02", "declared_lane": "post_cutoff_background", "matched_feature_ids": []},
                ]),
            ],
            "accepted": True, "detail_path": "critical_date", "detail_equals": "2026-01-01",
        },
    },
))
CAPABILITY_PROBES[("zju-paper-to-patent", "prior_art_separation")].update({"oracle_id": "patent_prior_art_lane_separation_v1", "variantize_cross": False})

chem_fields = {
    "chemical_identity": ("entities.0.inchi_key", "ABCDEFGHIJKLMN-ABCDEFGHIJ-A", "ZZZZZZZZZZZZZZ-YYYYYYYYYY-B", ""),
    "task_routing": ("records.0.source_database", "primary literature", "PubChem", ""),
    "evidence_strata": ("records.0.evidence_type", "experimental", "predicted", "unknown"),
    "condition_comparison": ("records.0.unit", "degC", "K", ""),
    "conflict_resolution": ("records.0.source_anchor", "Table 1", "Supplement Table S1", ""),
}
for cap, (field, pos, bound, neg) in chem_fields.items():
    register("zju-chemistry-databases", cap, field_probe(executor=EXECUTORS["zju-chemistry-databases"][0], adapter="valid", behavior=f"Validate chemistry {cap.replace('_', ' ')} with identity and source provenance.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=True, negative_signal=field.split(".")[-1], cross=pos))

CAPABILITY_PROBES[("zju-chemistry-databases", "chemical_identity")]["variants"]["negative"]["surface_contains"] = "identity anchor"
register("zju-chemistry-databases", "condition_comparison", custom_probe(
    executor="skills/zju-chemistry-databases/scripts/compare_conditions.py", adapter="chem_condition", base="chemistry_conditions",
    behavior="Normalize temperature, time, pressure, and text conditions before deciding whether chemistry records are comparable.",
    fields=["records.0.conditions", "records.1.conditions"], variants={
        "positive": {
            "mutations": [mutation("set", "records.1.conditions.pressure", {"value": 101325, "unit": "Pa"})],
            "accepted": True, "detail_path": "comparable_pairs", "detail_equals": 1,
        },
        "boundary": {
            "mutations": [mutation("set", "records.1.conditions.temperature", {"value": 26, "unit": "C"})],
            "accepted": True, "detail_path": "pairs.0.reasons", "detail_contains": "different_temperature",
        },
        "negative": {
            "mutations": [mutation("set", "records.1.conditions.temperature.unit", "F")],
            "accepted": False, "surface_contains": "unsupported or invalid temperature unit",
        },
        "cross_handoff": {
            "mutations": [mutation("set", "records.0.conditions.pressure", {"value": 1, "unit": "bar"}), mutation("set", "records.1.conditions.pressure", {"value": 100, "unit": "kPa"})],
            "accepted": True, "detail_path": "comparable_pairs", "detail_equals": 1,
        },
    },
))
CAPABILITY_PROBES[("zju-chemistry-databases", "condition_comparison")].update({"oracle_id": "chemistry_condition_normalization_v1", "variantize_cross": False})

integrity_fields = {
    "evidence_preservation": ("declared_text", "sample,value\nA,1\n", "sample,value\nA,1\n", "sample,value\nA,2\n"),
    "claim_provenance_audit": ("artifact_text", "sample,value\nA,1\n", "sample,value\nA,1\n", "sample,value\nA,999\n"),
    "neutral_triage": ("artifact_text", "sample,value\nA,1\n", "sample,value\nA,1\n", "sample,value\nA,999\n"),
    "scope_severity": ("declared_text", "sample,value\nA,1\n", "sample,value\nA,1\n", "sample,value\nA,2\n"),
}
for cap, (field, pos, bound, neg) in integrity_fields.items():
    register("zju-research-integrity", cap, field_probe(executor=EXECUTORS["zju-research-integrity"][0], adapter="integrity", behavior=f"Audit integrity {cap.replace('_', ' ')} using recomputed bytes and neutral findings.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=True, negative_signal="SHA-256 mismatch", cross=pos))

register("zju-research-integrity", "evidence_preservation", custom_probe(
    executor=EXECUTORS["zju-research-integrity"][0], adapter="integrity", base=None,
    behavior="Recompute artifact bytes and reject a declared hash that no longer preserves the original.",
    fields=["artifact_text", "declared_text"], variants={
        "positive": {"mutations": [mutation("set", "artifact_text", "sample,value\nA,1\n"), mutation("set", "declared_text", "sample,value\nA,1\n")], "accepted": True},
        "boundary": {"mutations": [mutation("set", "artifact_text", "sample,value\nA,2\n"), mutation("set", "declared_text", "sample,value\nA,2\n")], "accepted": True},
        "negative": {"mutations": [mutation("set", "artifact_text", "sample,value\nA,999\n"), mutation("set", "declared_text", "sample,value\nA,1\n")], "accepted": False, "surface_contains": "SHA-256 mismatch"},
        "cross_handoff": {"mutations": [mutation("set", "artifact_text", "sample,value\nA,3\n"), mutation("set", "declared_text", "sample,value\nA,3\n")], "accepted": True},
    },
))
register("zju-research-integrity", "claim_provenance_audit", custom_probe(
    executor=EXECUTORS["zju-research-integrity"][0], adapter="integrity", base=None,
    behavior="Verify that the claim-supporting artifact bytes still match the provenance manifest.",
    fields=["artifact_text", "declared_text"], variants={
        "positive": {"mutations": [mutation("set", "artifact_text", "claim,value\nC,1\n"), mutation("set", "declared_text", "claim,value\nC,1\n")], "accepted": True},
        "boundary": {"mutations": [mutation("set", "artifact_text", "claim,value\nC,2\n"), mutation("set", "declared_text", "claim,value\nC,2\n")], "accepted": True},
        "negative": {"mutations": [mutation("set", "artifact_text", "claim,value\nC,999\n"), mutation("set", "declared_text", "claim,value\nC,1\n")], "accepted": False, "surface_contains": "SHA-256 mismatch"},
        "cross_handoff": {"mutations": [mutation("set", "artifact_text", "claim,value\nC,3\n"), mutation("set", "declared_text", "claim,value\nC,3\n")], "accepted": True},
    },
))
register("zju-research-integrity", "neutral_triage", custom_probe(
    executor="skills/zju-research-integrity/scripts/triage_integrity_case.py", adapter="integrity_case", base="integrity_case",
    behavior="Separate observed facts, attributed statements, anomalies, and interpretations without declaring intent or misconduct.",
    fields=["triage_items"], variants={
        "positive": {
            "mutations": [mutation("set", "triage_items.0.text", "The archived and submitted image hashes are not identical.")],
            "accepted": True, "detail_path": "details.neutral_triage.items", "detail_equals": 2,
        },
        "boundary": {
            "mutations": [mutation("set", "triage_items", [
                {"item_id": "I1", "kind": "observed_fact", "text": "The archived image hash differs from the submitted image hash.", "source_ids": ["H1", "H2"]},
                {"item_id": "I2", "kind": "reported_statement", "text": "The analyst reported exporting the panel twice.", "source_ids": ["N1"], "attributed_to": "analyst note"},
                {"item_id": "I3", "kind": "interpretation", "text": "The mismatch may be consistent with a second export; intent is not assessed.", "source_ids": ["H1", "N1"]},
            ])],
            "accepted": True, "detail_path": "details.neutral_triage.kind_counts.interpretation", "detail_equals": 1,
        },
        "negative": {
            "mutations": [mutation("set", "triage_items.0.text", "The analyst committed misconduct.")],
            "accepted": False, "surface_contains": "must not declare intent or misconduct",
        },
        "cross_handoff": {
            "mutations": [mutation("set", "triage_items", [
                {"item_id": "IX1", "kind": "observed_fact", "text": "存档图像与提交图像的哈希值不同。", "source_ids": ["HX1", "HX2"]},
                {"item_id": "IX2", "kind": "reported_statement", "text": "实验记录载明该面板曾导出两次。", "source_ids": ["NX1"], "attributed_to": "实验记录"},
            ])],
            "accepted": True, "detail_path": "details.neutral_triage.items", "detail_equals": 2,
        },
    },
))
CAPABILITY_PROBES[("zju-research-integrity", "neutral_triage")].update({"oracle_id": "integrity_neutrality_contract_v1", "variantize_cross": False, "runner_extra": {"check": "neutral_triage"}})
register("zju-research-integrity", "scope_severity", custom_probe(
    executor="skills/zju-research-integrity/scripts/triage_integrity_case.py", adapter="integrity_case", base="integrity_case",
    behavior="Compute integrity severity and required scope domains from explicit case signals, then detect understatement or omitted scope.",
    fields=["signals", "declared_severity", "declared_scope"], variants={
        "positive": {
            "mutations": [mutation("set", "signals.data_security_breach", False), mutation("set", "signals.active_submission", True), mutation("set", "declared_severity", "medium"), mutation("set", "declared_scope", ["provenance", "publication"])],
            "accepted": True, "detail_path": "details.scope_severity.computed_severity", "detail_equals": "medium",
        },
        "boundary": {
            "mutations": [mutation("set", "signals", {"data_security_breach": False, "evidence_loss_risk": False, "regulated_research": False, "immediate_participant_danger": False, "immediate_environment_danger": False, "active_submission": False, "authorship_dispute": False, "citation_integrity": False}), mutation("set", "declared_severity", "low"), mutation("set", "declared_scope", ["provenance"])],
            "accepted": True, "detail_path": "details.scope_severity.computed_severity", "detail_equals": "low",
        },
        "negative": {
            "mutations": [mutation("set", "declared_severity", "low"), mutation("set", "declared_scope", ["provenance"])],
            "accepted": False, "surface_contains": "computed 'high'",
        },
        "cross_handoff": {
            "mutations": [mutation("set", "signals", {"data_security_breach": False, "evidence_loss_risk": False, "regulated_research": False, "immediate_participant_danger": True, "immediate_environment_danger": False, "active_submission": False, "authorship_dispute": False, "citation_integrity": False}), mutation("set", "declared_severity", "critical"), mutation("set", "declared_scope", ["provenance", "ethics"])],
            "accepted": True, "detail_path": "details.scope_severity.required_scope", "detail_contains": "ethics",
        },
    },
))
CAPABILITY_PROBES[("zju-research-integrity", "scope_severity")].update({"oracle_id": "integrity_scope_severity_matrix_v1", "variantize_cross": False, "runner_extra": {"check": "scope_severity"}})
register("zju-research-integrity", "remediation_escalation", custom_probe(
    executor="skills/zju-research-integrity/scripts/triage_integrity_case.py", adapter="integrity_case", base="integrity_case",
    behavior="Require proportionate preservation, access control, chronology, authorized referral, and emergency escalation while rejecting destructive actions.",
    fields=["signals", "proposed_actions"], variants={
        "positive": {
            "mutations": [mutation("set", "proposed_actions.3.authority", "ZJU research integrity office")],
            "accepted": True, "detail_path": "details.remediation_escalation.severity", "detail_equals": "high",
        },
        "boundary": {
            "mutations": [mutation("set", "signals", {"data_security_breach": False, "evidence_loss_risk": False, "regulated_research": False, "immediate_participant_danger": False, "immediate_environment_danger": False, "active_submission": False, "authorship_dispute": False, "citation_integrity": False}), mutation("set", "proposed_actions", [{"action": "preserve_evidence"}, {"action": "restrict_access"}])],
            "accepted": True, "detail_path": "details.remediation_escalation.severity", "detail_equals": "low",
        },
        "negative": {
            "mutations": [mutation("set", "proposed_actions", [{"action": "delete_originals"}])],
            "accepted": False, "surface_contains": "prohibited actions",
        },
        "cross_handoff": {
            "mutations": [
                mutation("set", "signals", {"data_security_breach": False, "evidence_loss_risk": False, "regulated_research": False, "immediate_participant_danger": True, "immediate_environment_danger": False, "active_submission": False, "authorship_dispute": False, "citation_integrity": False}),
                mutation("set", "proposed_actions", [
                    {"action": "preserve_evidence"}, {"action": "restrict_access"}, {"action": "document_chronology"},
                    {"action": "authorized_referral", "authority": "research integrity office"},
                    {"action": "emergency_channel", "authority": "institutional emergency response"},
                ]),
            ],
            "accepted": True, "detail_path": "details.remediation_escalation.severity", "detail_equals": "critical",
        },
    },
))
CAPABILITY_PROBES[("zju-research-integrity", "remediation_escalation")].update({"oracle_id": "integrity_remediation_escalation_v1", "variantize_cross": False, "runner_extra": {"check": "remediation_escalation"}})

director_fields = {
    "mission_state": ("research_question", "Which intervention improves Y?", "Which mechanism changes Y?", ""),
    "minimal_dag_routing": ("requested_deliverables", ["hypothesis_set"], ["paper_card"], []),
    "artifact_gate_truth": ("constraints.autonomy_ceiling", "L1", "L0", "L9"),
    "execution_provider": ("domain", "materials", "chemistry", "unknown-domain"),
    "cross_skill_release": ("current_stage", "framing", "synthesis", "unknown-stage"),
}
for cap, (field, pos, bound, neg) in director_fields.items():
    register("zju-research-director", cap, field_probe(executor=EXECUTORS["zju-research-director"][0], adapter="valid", behavior=f"Validate director {cap.replace('_', ' ')} in the versioned mission contract.", field=field, positive=pos, boundary=bound, negative=neg, negative_op="set", boundary_accepts=True, negative_signal=field.split(".")[-1], cross=pos))


def _variantize(value: Any, seed: int, key: str = "") -> Any:
    """Create semantically equivalent but independently hashed valid fixtures."""
    if isinstance(value, dict):
        return {k: _variantize(v, seed, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_variantize(item, seed, key[:-1] if key.endswith("s") else key) for item in value]
    if isinstance(value, str):
        if re.fullmatch(r"[a-f]", value) and key == "sha256":
            return value
        if key == "sha256" or re.fullmatch(r"[a-f0-9]{64}", value):
            return value
        if key.endswith("_id") or key in {"record_id", "run_id", "mission_id", "comment_id", "derived_from"}:
            return f"{value}-V{seed:02d}"
        if key.endswith("_ids"):
            return f"{value}-V{seed:02d}"
        if value.lower().startswith("10.") or "doi.org/10." in value.lower():
            return value.rstrip(".") + f"-v{seed:02d}" + ("." if value.endswith(".") else "")
        if key in {"title", "question", "claim", "statement", "research_question"}:
            return f"{value} Case {seed}."
    return value


def _negative_fixture(skill_id: str, fixture: dict[str, Any]) -> dict[str, Any]:
    bad = copy.deepcopy(fixture)
    if skill_id == "zju-literature-search":
        bad["records"] = "not-a-record-array"
    elif skill_id == "zju-fulltext-access":
        bad["password"] = "must-not-persist"
    elif skill_id == "zju-reference-audit":
        bad["submitted"] = "10.1000/wrong"
    elif skill_id == "zju-paper-reader":
        bad["source_type"] = "fake_pdf"
    elif skill_id == "zju-experiment-log":
        bad.pop("started_at", None)
    elif skill_id == "zju-statistics-audit":
        bad["contract"]["analyses"][0]["execution"]["method"] = "unsupported_mixed_model"
    elif skill_id == "zju-scientific-writing":
        bad["claims"][0].pop("evidence_ids", None)
    elif skill_id == "zju-literature-monitor":
        bad["state"]["token"] = "secret"
    elif skill_id == "zju-evidence-synthesis":
        bad["rows"][0]["uncertainty"] = None
    elif skill_id == "zju-hypothesis-design":
        bad["hypotheses"] = bad["hypotheses"][:1]
        bad["experiments"] = []
    elif skill_id == "zju-scientific-figure":
        bad["tamper_data_after_registry"] = True
    elif skill_id == "zju-paper2ppt":
        bad["duration_minutes"] = 0.5
    elif skill_id == "zju-reviewer":
        bad["concerns"][0].update({"severity": "minor", "blocking": True})
    elif skill_id == "zju-review-response":
        bad["items"][0]["location"] = "LOCATION_PENDING"
    elif skill_id == "zju-data-availability":
        bad["artifacts"][0].pop("identifier", None)
    elif skill_id == "zju-proposal-writer":
        bad["scheme_status"] = "template_pending"
    elif skill_id == "zju-paper-to-patent":
        bad["features"][0]["support_state"] = "unsupported"
    elif skill_id == "zju-chemistry-databases":
        bad["records"][0]["entity_id"] = "MISSING"
    elif skill_id == "zju-research-integrity":
        bad["artifact_text"] = "sample,value\nA,999\n"
    elif skill_id == "zju-research-director":
        bad["research_question"] = ""
    else:  # pragma: no cover - guarded by portfolio census
        raise KeyError(skill_id)
    return bad


def _path_parts(path: str) -> list[str | int]:
    return [int(part) if part.isdigit() else part for part in path.split(".")]


def _mutate_payload(payload: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    value = copy.deepcopy(payload)
    for operation in operations:
        parts = _path_parts(operation["path"])
        current: Any = value
        for part in parts[:-1]:
            current = current[part]
        leaf = parts[-1]
        if operation["op"] == "set":
            current[leaf] = copy.deepcopy(operation.get("value"))
        elif operation["op"] == "delete":
            if isinstance(current, list):
                del current[leaf]
            else:
                current.pop(leaf, None)
        else:
            raise RuntimeError(f"unsupported mutation operation: {operation['op']}")
    return value


def _probe_base(skill_id: str, probe: dict[str, Any]) -> dict[str, Any]:
    base_id = probe.get("base")
    return copy.deepcopy(PROBE_BASES[base_id] if base_id else BASES[skill_id])


def build_cases() -> dict[str, Any]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    variant_order = ("positive", "boundary", "negative", "cross_handoff")
    for skill in matrix["skills"]:
        skill_id = skill["skill_id"]
        for cap_index, capability in enumerate(skill["capabilities"], 1):
            capability_id = capability["id"]
            blocker = BLOCKED_CAPABILITIES.get((skill_id, capability_id))
            probe = CAPABILITY_PROBES.get((skill_id, capability_id))
            if not blocker and not probe:
                raise RuntimeError(f"missing capability-specific L2 probe for {skill_id}/{capability_id}")
            for variant_index, variant in enumerate(variant_order, 1):
                case_id = f"L2-{skill_id[4:].upper().replace('-', '_')}-{cap_index:02d}-{variant_index:02d}"
                if blocker:
                    fixture = {"blocked_capability": capability_id, "blocked_reason": blocker, "variant": variant}
                    fixture_sha = canonical_sha256(fixture)
                    cases.append({
                        "case_id": case_id, "skill_id": skill_id, "capability_ids": [capability_id],
                        "stratum": variant, "variant": variant, "eligibility": "blocked_no_deterministic_oracle",
                        "blocked_reason": blocker, "fixture": fixture, "fixture_sha256": fixture_sha,
                        "measured_behavior": None, "oracle_id": None, "oracle": None,
                        "claim_boundary": "Blocked cases are planning records only and never become scorer evidence.",
                    })
                    continue
                variant_spec = probe["variants"][variant]
                base = _probe_base(skill_id, probe)
                if variant == "cross_handoff" and probe.get("variantize_cross", True):
                    # Cross-handoff cases use a second, semantically equivalent
                    # identity set. The executor consumes these IDs/text fields;
                    # this is not a case-label-only salt.
                    base = _variantize(base, cap_index * 10 + variant_index)
                test_input = _mutate_payload(base, variant_spec["mutations"])
                candidate_controls = sorted(
                    (name for name in variant_order if name != variant),
                    key=lambda name: probe["variants"][name]["accepted"] == variant_spec["accepted"],
                )
                control_variant = next(
                    name for name in candidate_controls
                    if _mutate_payload(base, probe["variants"][name]["mutations"]) != test_input
                )
                control_input = _mutate_payload(base, probe["variants"][control_variant]["mutations"])
                fixture = {"control_input": control_input, "test_input": test_input}
                fixture_sha = canonical_sha256(fixture)
                assertions = [{"path": "test.accepted", "op": "eq", "value": bool(variant_spec["accepted"])}]
                assertions.append({
                    "path": "control.accepted", "op": "eq",
                    "value": bool(probe["variants"][control_variant]["accepted"]),
                })
                if variant_spec.get("surface_contains"):
                    assertions.append({"path": "test.surface", "op": "contains", "value": variant_spec["surface_contains"]})
                if variant_spec.get("detail_path"):
                    assertion = {"path": "test.details." + variant_spec["detail_path"], "op": "contains" if "detail_contains" in variant_spec else "eq", "value": variant_spec.get("detail_contains", variant_spec.get("detail_equals"))}
                    assertions.append(assertion)
                oracle_id = probe.get("oracle_id", f"{skill_id}:{capability_id}")
                if probe.get("oracle_id"):
                    assertions.append({"path": "test.details.oracle_id", "op": "eq", "value": oracle_id})
                runner = {"path": "evals/l2_portfolio_execution.py", "adapter": probe["adapter"], "executor_path": probe["executor"]}
                runner.update(probe.get("runner_extra", {}))
                cases.append({
                    "case_id": case_id, "skill_id": skill_id, "capability_ids": [capability_id],
                    "stratum": "nominal" if variant == "positive" else variant,
                    "variant": variant, "eligibility": "executable",
                    "fixture": fixture, "fixture_sha256": fixture_sha,
                    "input_mutations": variant_spec["mutations"],
                    "capability_input_fields": probe["capability_input_fields"],
                    "measured_behavior": probe["measured_behavior"],
                    "oracle_id": oracle_id,
                    "oracle": {"capability_id": capability_id, "assertions": assertions},
                    "runner": runner,
                    "claim_boundary": "Deterministic software behavior only; not scientific prose or factual correctness.",
                })
    document = {
        "schema_version": "3.0",
        "suite_id": "zju-l2-deterministic-portfolio-v3",
        "layer": LAYER,
        "minimum_cases_per_skill": 20,
        "minimum_negative_or_boundary_cases_per_skill": 5,
        "case_count": len(cases),
        "blocked_case_count": sum(case.get("eligibility") != "executable" for case in cases),
        "cases": cases,
    }
    return document


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _run_input(case: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    """Run one frozen input and expose only deterministic, oracle-visible output."""
    executor = ROOT / case["runner"]["executor_path"]
    module = load_module(executor)
    adapter = case["runner"]["adapter"]
    try:
        if adapter == "saturation":
            details = module.assess(fixture)
            accepted = isinstance(details, dict)
        elif adapter == "literature":
            rows = module.deduplicate(fixture["records"])
            details = {
                "record_count": len(rows),
                "identifier_missing_count": sum(bool(row.get("identifier_missing")) for row in rows),
                "identity_missing_count": sum(bool(row.get("identity_missing")) for row in rows),
                "identity_keys": [row.get("identity_key") for row in rows],
                "query_ids": sorted({item for row in rows for item in row.get("query_ids", [])}),
                "source_databases": sorted({item for row in rows for item in row.get("source_database", [])}),
            }
            accepted = True
        elif adapter == "fulltext":
            details = module.classify(fixture)
            accepted = details.get("status") != "blocked_sensitive_input"
        elif adapter == "reference":
            details = module.compare_field(fixture["field"], fixture["submitted"], fixture["canonical"])
            accepted = details.get("status") == "match"
        elif adapter == "reference_audit":
            difference = module.compare_field(fixture["field"], fixture["submitted"], fixture["canonical"])
            accepted = difference.get("status") != "not_verified"
            details = {
                "status": "verified" if difference.get("status") == "match" else "repair_required" if accepted else "not_verified",
                "field_differences": {fixture["field"]: difference},
                "human_confirmation_required": difference.get("status") != "match",
            }
        elif adapter == "status_freshness":
            details = module.validate(fixture["records"], date.fromisoformat(fixture["as_of"]), fixture["max_age_days"])
            accepted = bool(details.get("valid"))
        elif adapter == "claim_support":
            details = module.audit(fixture)
            accepted = bool(details.get("valid"))
        elif adapter in {"append_log", "writing_mode", "data_statement"}:
            details = module.validate(fixture)
            accepted = bool(details.get("valid"))
        elif adapter == "protocol_select":
            details = module.select(fixture)
            accepted = bool(details.get("valid"))
        elif adapter in {"feasibility", "patent_claim_map"}:
            details = module.audit(fixture)
            accepted = bool(details.get("valid"))
        elif adapter == "prior_art":
            details = module.separate(fixture)
            accepted = bool(details.get("valid"))
        elif adapter == "chem_condition":
            details = module.compare(fixture)
            accepted = bool(details.get("valid"))
        elif adapter == "integrity_case":
            details = module.audit(fixture, check=case["runner"]["check"])
            accepted = bool(details.get("valid"))
        elif adapter == "reader":
            with tempfile.TemporaryDirectory() as temporary:
                suffix = ".pdf" if fixture["source_type"] == "fake_pdf" else ".md"
                path = Path(temporary) / ("source" + suffix)
                path.write_text(fixture["source_text"], encoding="utf-8")
                output = module.prepare_source(path)
                accepted = output.get("page_count", 0) >= 1 and bool(output.get("pages", [{}])[0].get("anchor"))
                details = output
        elif adapter == "reader_contract":
            details = module.validate(fixture["content"], fixture["mode"])
            accepted = bool(details.get("valid"))
        elif adapter == "valid":
            if case["skill_id"] == "zju-evidence-synthesis" and set(fixture) == {"rows"}:
                validator_input = fixture["rows"]
            else:
                validator_input = fixture
            details = module.validate(validator_input)
            accepted = bool(details.get("valid"))
        elif adapter == "writing":
            details = module.validate(fixture["claims"])
            accepted = bool(details.get("valid"))
        elif adapter == "monitor":
            output = module.update(fixture["state"], fixture["records"], fixture["observed_at"])
            details = {"counts": output.get("counts"), "handoff_queue": output.get("handoff_queue"), "state": output.get("state")}
            accepted = bool(output.get("counts", {}).get("new") or output.get("counts", {}).get("updated"))
        elif adapter == "analysis_contract":
            details = module.validate(fixture["contract"])
            accepted = bool(details.get("valid"))
        elif adapter == "statistics":
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                data_path, contract_path = base / "data.csv", base / "contract.json"
                _write_csv(data_path, fixture["rows"])
                contract_path.write_text(json.dumps(fixture["contract"], ensure_ascii=False), encoding="utf-8")
                registry, _run = module.execute(data_path, contract_path)
                result = registry.get("results", [{}])[0]
                details = {
                    "result_id": result.get("result_id"), "estimate": result.get("estimate"),
                    "ci": result.get("ci"), "p_value": result.get("p_value"), "n": result.get("n"),
                    "diagnostics": result.get("diagnostics"), "sensitivity_analyses": result.get("sensitivity_analyses"),
                }
                accepted = len(registry.get("results", [])) == 1
        elif adapter == "registry_reconcile":
            details = module.validate(fixture["registry"], fixture.get("analysis_contract"))
            accepted = bool(details.get("valid"))
        elif adapter == "writing_contract":
            details = module.validate(fixture)
            accepted = bool(details.get("valid"))
        elif adapter == "conflict_matrix":
            details = module.build(fixture["rows"])
            accepted = isinstance(details.get("summary"), dict)
        elif adapter == "figure":
            analysis = load_module(ROOT / "skills/zju-statistics-audit/scripts/execute_analysis.py")
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                rows = fixture["rows"]
                data_path, contract_path = base / "data.csv", base / "contract.json"
                _write_csv(data_path, rows)
                contract = copy.deepcopy(BASES["zju-statistics-audit"]["contract"])
                contract["analyses"][0].update({"model_family": "simple_ols", "effect_measure": "slope"})
                contract["analyses"][0]["execution"] = {
                    "method": "simple_ols", "result_id": "RES-1", "x_column": "dose", "y_column": "response",
                    "experimental_unit_column": "unit", "unit": "AU/AU",
                }
                contract_path.write_text(json.dumps(contract), encoding="utf-8")
                registry, run = analysis.execute(data_path, contract_path)
                paths = analysis.write_outputs(registry, run, base / "analysis")
                render_data = data_path
                if fixture.get("tamper_data_after_registry"):
                    altered = [dict(row) for row in rows]
                    altered[0]["response"] = 999.0
                    render_data = base / "altered.csv"
                    _write_csv(render_data, altered)
                spec_path = base / "spec.json"
                spec_path.write_text(json.dumps(fixture["spec"]), encoding="utf-8")
                manifest, manifest_path = module.render(render_data, paths["registry"], spec_path, base / "figure")
                details = {
                    "export_formats": [row["format"] for row in manifest.get("exports", [])],
                    "exports_content_hashed": all(bool(row.get("sha256")) for row in manifest.get("exports", [])),
                    "manifest_has_provenance": len(manifest.get("source_files", [])) == 3,
                    "manifest_written": manifest_path.is_file(),
                }
                accepted = manifest_path.is_file() and bool(manifest.get("exports"))
        elif adapter == "deck_artifact":
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                plan_path, deck_path = base / "plan.json", base / "deck.pptx"
                plan_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
                manifest = module.build_presentation(plan_path, deck_path)
                details = {
                    "slide_count": manifest.get("slide_count"),
                    "source_anchors": manifest.get("source_anchors"),
                    "artifact_format": manifest.get("artifact", {}).get("format"),
                    "artifact_content_hashed": bool(manifest.get("artifact", {}).get("sha256")),
                    "ooxml_reopen": manifest.get("qa", {}).get("ooxml_reopen"),
                    "speaker_notes_present": manifest.get("qa", {}).get("speaker_notes_present"),
                }
                accepted = deck_path.is_file() and manifest.get("qa", {}).get("ooxml_reopen") == "passed"
        elif adapter == "integrity":
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                artifact = base / "raw.csv"
                artifact.write_bytes(fixture["artifact_text"].encode("utf-8"))
                declared_sha = hashlib.sha256(fixture["declared_text"].encode("utf-8")).hexdigest()
                payload = {"artifacts": [{
                    "artifact_id": "A1", "path": "raw.csv", "role": "raw",
                    "created_at": "2026-08-11T09:00:00+08:00", "custodian": "researcher",
                    "preservation_status": "immutable_original", "sha256": declared_sha,
                }], "links": []}
                output = module.validate(payload, base_dir=base)
                accepted = bool(output.get("valid"))
                details = {key: value for key, value in output.items() if key != "base_dir"}
        else:  # pragma: no cover
            raise RuntimeError(f"unknown adapter {adapter}")
        return {
            "accepted": bool(accepted), "observed": "accepted" if accepted else "rejected",
            "details": details, "surface": json.dumps(details, ensure_ascii=False, sort_keys=True), "exception": None,
        }
    except Exception as exc:
        message = re.sub(r"(?i)(?:[A-Z]:)?[^\s\"']*[\\/]Temp[\\/]tmp[^\\/\s\"']+", "<TEMP>", str(exc))
        return {
            "accepted": False, "observed": "rejected", "details": {},
            "surface": f"{type(exc).__name__}: {message}",
            "exception": {"type": type(exc).__name__, "message": message},
        }


def _get_dotted(value: Any, path: str) -> Any:
    current = value
    for part in _path_parts(path):
        current = current[part]
    return current


def _oracle_assertion(value: dict[str, Any], assertion: dict[str, Any]) -> dict[str, Any]:
    try:
        observed = _get_dotted(value, assertion["path"])
    except (KeyError, IndexError, TypeError) as exc:
        return {"passed": False, "observed": None, "error": f"path unavailable: {type(exc).__name__}"}
    expected = assertion.get("value")
    if assertion["op"] == "eq":
        passed = observed == expected
    elif assertion["op"] == "contains":
        passed = expected in observed
    elif assertion["op"] == "ne":
        passed = observed != expected
    else:  # pragma: no cover - case manifest validation rejects this
        raise RuntimeError(f"unsupported oracle operation: {assertion['op']}")
    return {"passed": bool(passed), "observed": observed, "expected": expected}


def _execute_once(case: dict[str, Any]) -> dict[str, Any]:
    control = _run_input(case, copy.deepcopy(case["fixture"]["control_input"]))
    test = _run_input(case, copy.deepcopy(case["fixture"]["test_input"]))
    combined = {"control": control, "test": test}
    checks = [_oracle_assertion(combined, assertion) for assertion in case["oracle"]["assertions"]]
    return {"matched": bool(checks and all(row["passed"] for row in checks)), "checks": checks, **combined}


def execute_case(case: dict[str, Any]) -> dict[str, Any]:
    if case.get("eligibility") != "executable":
        return {
            "passed": False, "blocked": True, "reason": case.get("blocked_reason", "not executable"),
            "fixture_sha256": canonical_sha256(case["fixture"]),
        }
    actual_fixture_sha = canonical_sha256(case["fixture"])
    if actual_fixture_sha != case["fixture_sha256"]:
        return {"passed": False, "reason": "fixture hash mismatch", "fixture_sha256": actual_fixture_sha}
    first = _execute_once(case)
    second = _execute_once(case)
    deterministic = canonical_sha256(first) == canonical_sha256(second)
    return {
        "passed": bool(first["matched"] and second["matched"] and deterministic),
        "fixture_sha256": actual_fixture_sha,
        "deterministic_repeat": deterministic,
        "first": first,
        "second_sha256": canonical_sha256(second),
    }


def _record(case: dict[str, Any], observation: dict[str, Any], commit: str, protocol_sha: str, matrix_sha: str) -> dict[str, Any]:
    executor_path = ROOT / case["runner"]["executor_path"]
    evidence_payload = {
        "case_id": case["case_id"], "fixture_sha256": case["fixture_sha256"],
        "executor_sha256": sha256_file(executor_path), "observation": observation,
    }
    return {
        "skill_id": case["skill_id"], "layer": LAYER, "case_id": case["case_id"],
        "case_fingerprint": canonical_sha256({
            "skill_id": case["skill_id"], "case_id": case["case_id"],
            "fixture_sha256": case["fixture_sha256"], "oracle": case["oracle"],
        }),
        "stratum": case["stratum"], "capability_ids": case["capability_ids"],
        "critical_failure": False, "passed": bool(observation["passed"]), "run_id": RUN_ID,
        "skill_commit": commit, "protocol_sha256": protocol_sha, "capability_matrix_sha256": matrix_sha,
        "executor_sha256": sha256_file(executor_path), "evidence_sha256": canonical_sha256(evidence_payload),
    }


def run_suite(case_document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol_sha = canonical_sha256(json.loads(PROTOCOL_PATH.read_text(encoding="utf-8")))
    matrix_sha = canonical_sha256(json.loads(MATRIX_PATH.read_text(encoding="utf-8")))
    commit = git_commit()
    observations: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    for case in case_document["cases"]:
        if case.get("eligibility") != "executable":
            observations.append({
                "case_id": case["case_id"], "skill_id": case["skill_id"],
                "passed": False, "blocked": True,
                "reason": case.get("blocked_reason", "no deterministic oracle"),
            })
            continue
        observation = execute_case(case)
        observation_document = {
            "schema_version": "3.0", "run_id": RUN_ID, "case_id": case["case_id"],
            "skill_id": case["skill_id"], "fixture_sha256": case["fixture_sha256"],
            "runner_sha256": sha256_file(Path(__file__)), "observation": observation,
        }
        evidence_path = EVIDENCE_DIR / f"{case['case_id']}.json"
        evidence_path.write_text(json.dumps(observation_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        observations.append({"case_id": case["case_id"], "skill_id": case["skill_id"], **observation})
        records.append(_record(case, observation, commit, protocol_sha, matrix_sha))
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in observations:
        counts[row["skill_id"]]["planned"] += 1
        if row.get("blocked"):
            counts[row["skill_id"]]["blocked"] += 1
        else:
            counts[row["skill_id"]]["executable"] += 1
            counts[row["skill_id"]]["passed"] += int(row["passed"])
            counts[row["skill_id"]]["failed"] += int(not row["passed"])
    executable = [row for row in observations if not row.get("blocked")]
    blocked = [row for row in observations if row.get("blocked")]
    summary = {
        "schema_version": "3.0", "suite_id": case_document["suite_id"], "run_id": RUN_ID,
        "case_document_sha256": canonical_sha256(case_document), "runner_sha256": sha256_file(Path(__file__)),
        "skill_commit": commit, "planned_case_count": len(observations),
        "executable_case_count": len(executable), "blocked_case_count": len(blocked),
        "passed": sum(row["passed"] for row in executable),
        "failed": sum(not row["passed"] for row in executable),
        "by_skill": {skill: dict(counter) for skill, counter in sorted(counts.items())},
        "claim_boundary": "L2 deterministic function evidence only; no scientific correctness or free-form task-quality claim.",
    }
    bundle = {
        "$schema": "portfolio-results-v3.schema.json", "schema_version": "3.0",
        "protocol_id": "zju-portfolio-twenty-skill-v3", "run_id": RUN_ID, "skill_commit": commit,
        "protocol_sha256": protocol_sha, "capability_matrix_sha256": matrix_sha, "run_manifest_sha256": None,
        "baseline_selection": {}, "rater_precommit": None,
        "holdout_declaration": {"frozen": False, "unseen": False, "first_attempt": False, "independent_administration": False, "lock_sha256": None, "case_document_sha256": None, "case_fingerprints_sha256": None, "first_attempt_registry_sha256": None, "allocation_commitment_sha256": None, "allocation_reveal_sha256": None, "independent_administration_attestation_sha256": None},
        "protocol_deviations": [], "records": records,
    }
    return summary, bundle


def combine_with_l1(l2_bundle: dict[str, Any]) -> dict[str, Any]:
    # Rebuild scorer-ready L1 records from the immutable census.  Never migrate
    # the previous combined bundle: hashing that bundle creates a recursive
    # self-reference and changes the bytes on every otherwise identical run.
    census = json.loads((ROOT / "evals/l1-contract-census-v3.json").read_text(encoding="utf-8"))
    records = []
    for source in census["records"]:
        fingerprint = hashlib.sha256(
            f"{source['skill_id']}\0{source['case_id']}\0{census['input_tree_sha256']}".encode("utf-8")
        ).hexdigest()
        evidence_hash = canonical_sha256({
            "skill_id": source["skill_id"],
            "case_id": source["case_id"],
            "passed": source["passed"],
            "input_tree_sha256": census["input_tree_sha256"],
        })
        records.append({
            **source,
            "run_id": l2_bundle["run_id"],
            "skill_commit": l2_bundle["skill_commit"],
            "protocol_sha256": l2_bundle["protocol_sha256"],
            "capability_matrix_sha256": l2_bundle["capability_matrix_sha256"],
            "case_fingerprint": fingerprint,
            "evidence_sha256": evidence_hash,
        })
    records.extend(l2_bundle["records"])
    return {**l2_bundle, "records": records}


def validate_case_document(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    expected = {row["skill_id"]: {cap["id"] for cap in row["capabilities"]} for row in matrix["skills"]}
    by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    capability_signatures: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    seen_ids, seen_hashes = set(), set()
    for case in document.get("cases", []):
        by_skill[case.get("skill_id")].append(case)
        if case.get("case_id") in seen_ids:
            errors.append(f"duplicate case_id: {case.get('case_id')}")
        seen_ids.add(case.get("case_id"))
        actual_hash = canonical_sha256(case.get("fixture"))
        if case.get("fixture_sha256") != actual_hash:
            errors.append(f"fixture hash mismatch: {case.get('case_id')}")
        if actual_hash in seen_hashes:
            errors.append(f"fixture reused byte-for-byte: {case.get('case_id')}")
        seen_hashes.add(actual_hash)
        eligibility = case.get("eligibility")
        if eligibility == "executable":
            if not case.get("measured_behavior") or not case.get("oracle_id") or not case.get("oracle"):
                errors.append(f"executable case lacks measured behavior/oracle: {case.get('case_id')}")
            if not case.get("input_mutations") or not case.get("capability_input_fields"):
                errors.append(f"executable case lacks capability-specific input mutation: {case.get('case_id')}")
            capability_ids = case.get("capability_ids", [])
            if len(capability_ids) == 1:
                capability_signatures[(case.get("skill_id"), capability_ids[0])].add(
                    (case.get("measured_behavior"), case.get("oracle_id"))
                )
            fixture = case.get("fixture", {})
            if fixture.get("test_input") == fixture.get("control_input"):
                errors.append(f"test and control inputs are identical: {case.get('case_id')}")
            mutation_paths = {row.get("path") for row in case.get("input_mutations", []) if isinstance(row, dict)}
            capability_fields = set(case.get("capability_input_fields", []))
            if not any(
                mutation == field
                or (isinstance(mutation, str) and mutation.startswith(field + "."))
                or (isinstance(mutation, str) and field.startswith(mutation + "."))
                for mutation in mutation_paths
                for field in capability_fields
            ):
                errors.append(f"mutations do not touch declared capability fields: {case.get('case_id')}")
            runner = case.get("runner", {})
            executor_path = runner.get("executor_path")
            if not isinstance(executor_path, str) or not (ROOT / executor_path).is_file():
                errors.append(f"executor path unavailable: {case.get('case_id')}")
            if not isinstance(runner.get("adapter"), str) or not runner.get("adapter"):
                errors.append(f"runner adapter unavailable: {case.get('case_id')}")
            assertions = case.get("oracle", {}).get("assertions", [])
            if not any(row.get("path") == "test.accepted" for row in assertions):
                errors.append(f"case lacks observable test acceptance oracle: {case.get('case_id')}")
            if not any(row.get("path") == "control.accepted" for row in assertions):
                errors.append(f"case lacks observable control acceptance oracle: {case.get('case_id')}")
        elif eligibility != "blocked_no_deterministic_oracle":
            errors.append(f"unknown eligibility: {case.get('case_id')}")
    if document.get("case_count") != len(document.get("cases", [])):
        errors.append("case_count does not match serialized cases")
    actual_blocked = sum(case.get("eligibility") != "executable" for case in document.get("cases", []))
    if document.get("blocked_case_count") != actual_blocked:
        errors.append("blocked_case_count does not match serialized cases")
    if set(by_skill) != set(expected):
        errors.append("case document must cover exactly all twenty expected skills")
    for skill_id, capabilities in expected.items():
        rows = by_skill.get(skill_id, [])
        if len(rows) != 20:
            errors.append(f"{skill_id}: expected exactly twenty planned cases")
        boundary = sum(row.get("stratum") in {"negative", "boundary", "negative_or_boundary"} for row in rows)
        if boundary < 10:
            errors.append(f"{skill_id}: fewer than ten planned negative/boundary cases")
        negative = sum(row.get("variant") == "negative" for row in rows)
        if negative < 5:
            errors.append(f"{skill_id}: fewer than five negative cases")
        covered = {cap for row in rows for cap in row.get("capability_ids", [])}
        if not capabilities.issubset(covered):
            errors.append(f"{skill_id}: missing capability coverage {sorted(capabilities - covered)}")
        counts = Counter(cap for row in rows for cap in row.get("capability_ids", []))
        if any(counts[capability] != 4 for capability in capabilities):
            errors.append(f"{skill_id}: every capability must have four planned variants")
        for capability in capabilities:
            if len(capability_signatures.get((skill_id, capability), set())) != 1:
                errors.append(f"{skill_id}/{capability}: variants do not share one capability-specific behavior and oracle")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "run", "verify"])
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--summary", type=Path, default=ROOT / "evals/evidence/l2-v3-summary.json")
    parser.add_argument("--bundle", type=Path, default=ROOT / "evals/l2-portfolio-evidence-v3.json")
    parser.add_argument("--combined", type=Path, default=ROOT / "evals/current-portfolio-evidence-v3.json")
    args = parser.parse_args(argv)
    if args.command == "build":
        document = build_cases()
        args.cases.parent.mkdir(parents=True, exist_ok=True)
        args.cases.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"case_count": document["case_count"], "sha256": canonical_sha256(document)}))
        return 0
    document = json.loads(args.cases.read_text(encoding="utf-8"))
    errors = validate_case_document(document)
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    if args.command == "verify":
        print(json.dumps({"valid": True, "case_count": len(document["cases"])}))
        return 0
    summary, bundle = run_suite(document)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.bundle.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    combined = combine_with_l1(bundle)
    args.combined.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
