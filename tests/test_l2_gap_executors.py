from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


claim_support = load("claim_support_gap", "skills/zju-reference-audit/scripts/audit_claim_support.py")
append_only = load("append_only_gap", "skills/zju-experiment-log/scripts/validate_append_only_log.py")
mode_fidelity = load("mode_fidelity_gap", "skills/zju-scientific-writing/scripts/validate_mode_fidelity.py")
protocol = load("protocol_gap", "skills/zju-evidence-synthesis/scripts/select_protocol.py")
statement = load("statement_gap", "skills/zju-data-availability/scripts/validate_statement_consistency.py")
feasibility = load("feasibility_gap", "skills/zju-proposal-writer/scripts/audit_feasibility.py")
claim_map = load("claim_map_gap", "skills/zju-paper-to-patent/scripts/audit_claim_map.py")
prior_art = load("prior_art_gap", "skills/zju-paper-to-patent/scripts/separate_prior_art.py")
conditions = load("conditions_gap", "skills/zju-chemistry-databases/scripts/compare_conditions.py")
integrity = load("integrity_gap", "skills/zju-research-integrity/scripts/triage_integrity_case.py")


def supported_claim():
    return {
        "evidence_facts": [{"fact_id": "F1", "key": "response_rate", "value": 42, "unit": "%", "source_id": "S1", "source_anchor": "Table 2"}],
        "claims": [{"claim_id": "C1", "atoms": [{"atom_id": "A1", "key": "response_rate", "value": 42, "unit": "%", "evidence_fact_ids": ["F1"], "declared_status": "supported"}]}],
    }


def polish_payload():
    assertion = {"assertion_id": "A1", "subject": "treatment", "predicate": "increased", "value": 2.5, "unit": "fold", "direction": "increase", "citation_ids": ["R1"], "wording": "original"}
    revised = {**assertion, "wording": "clearer prose"}
    return {"mode": "polish", "declared_mode": "polish", "source_assertions": [assertion], "output_assertions": [revised]}


def feasibility_payload():
    return {
        "project_weeks": 8,
        "resource_capacities": {"fte": 1.0},
        "work_packages": [
            {"wp_id": "WP1", "start_week": 1, "end_week": 3, "dependencies": [], "resource_demand": {"fte": 1.0}, "owner": "PI", "critical": True},
            {"wp_id": "WP2", "start_week": 4, "end_week": 6, "dependencies": ["WP1"], "resource_demand": {"fte": 1.0}, "owner": "RA", "critical": False},
        ],
        "milestones": [{"milestone_id": "M1", "wp_id": "WP1", "due_week": 3, "decision_rule": "advance if assay CV <= 10%"}],
        "risks": [{"risk_id": "R1", "wp_ids": ["WP1"], "probability": "medium", "impact": "high", "trigger": "CV > 10%", "mitigation": "pilot calibration", "contingency": "repeat with reference material", "owner": "PI"}],
    }


def patent_map_payload():
    return {
        "features": [
            {"feature_id": "F1", "support_state": "explicit", "source_anchors": ["Figure 2"]},
            {"feature_id": "F2", "support_state": "explicit", "source_anchors": ["Experiment 4"]},
        ],
        "claims": [
            {"claim_id": "C1", "role": "independent", "parent_claim_ids": [], "feature_ids": ["F1"]},
            {"claim_id": "C2", "role": "dependent", "parent_claim_ids": ["C1"], "feature_ids": ["F1", "F2"]},
        ],
    }


def condition_records():
    return {
        "records": [
            {"record_id": "R1", "entity_id": "InChIKey-X", "endpoint": "yield", "basis": "isolated", "conditions": {"temperature": {"value": 25, "unit": "C"}, "time": {"value": 1, "unit": "h"}, "pressure": {"value": 1, "unit": "atm"}, "solvent": "water", "catalyst": "Pd", "atmosphere": "N2"}},
            {"record_id": "R2", "entity_id": "InChIKey-X", "endpoint": "yield", "basis": "isolated", "conditions": {"temperature": {"value": 298.15, "unit": "K"}, "time": {"value": 60, "unit": "min"}, "pressure": {"value": 101.325, "unit": "kPa"}, "solvent": "Water", "catalyst": "pd", "atmosphere": "n2"}},
        ]
    }


def integrity_payload():
    return {
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
    }


class ClaimSupportTests(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(claim_support.audit(supported_claim())["valid"])

    def test_boundary_nonmaterial_atom_can_remain_unsupported(self):
        payload = supported_claim()
        payload["claims"][0]["atoms"].append({"atom_id": "A2", "key": "exploratory_note", "value": "possible", "evidence_fact_ids": [], "declared_status": "unsupported", "material": False})
        self.assertTrue(claim_support.audit(payload)["valid"])

    def test_negative_overclaim_is_detected(self):
        payload = supported_claim()
        payload["claims"][0]["atoms"][0]["value"] = 84
        result = claim_support.audit(payload)
        self.assertFalse(result["valid"])
        self.assertEqual(result["claims"][0]["atoms"][0]["status"], "contradicted")


class AppendOnlyTests(unittest.TestCase):
    def _corrected(self):
        raw = {"event_id": "E1", "event_type": "entry", "timestamp": "2026-08-15T09:00:00+08:00", "payload": {"temperature_c": 25}}
        first = append_only.seal([raw])[0]
        correction = {"event_id": "E2", "event_type": "correction", "timestamp": "2026-08-15T09:01:00+08:00", "supersedes_event_id": "E1", "original_event_hash": first["event_hash"], "patch": {"temperature_c": 26}, "reason": "instrument display transcription corrected"}
        return append_only.seal([raw, correction])

    def test_positive(self):
        self.assertTrue(append_only.validate({"events": self._corrected()})["valid"])

    def test_boundary_correction_can_itself_be_corrected(self):
        first_two = self._corrected()
        raw_first = {key: value for key, value in first_two[0].items() if key not in {"previous_event_hash", "event_hash"}}
        raw_second = {key: value for key, value in first_two[1].items() if key not in {"previous_event_hash", "event_hash"}}
        third = {"event_id": "E3", "event_type": "correction", "timestamp": "2026-08-15T09:02:00+08:00", "supersedes_event_id": "E2", "original_event_hash": first_two[1]["event_hash"], "patch": {"temperature_c": 25.8}, "reason": "calibration certificate applied"}
        self.assertTrue(append_only.validate({"events": append_only.seal([raw_first, raw_second, third])})["valid"])

    def test_negative_original_mutation_breaks_hash(self):
        events = self._corrected()
        events[0]["payload"]["temperature_c"] = 99
        self.assertFalse(append_only.validate({"events": events})["valid"])


class ModeFidelityTests(unittest.TestCase):
    def test_positive_polish(self):
        self.assertTrue(mode_fidelity.validate(polish_payload())["valid"])

    def test_boundary_draft_requires_verified_ledger(self):
        payload = {"mode": "draft", "output_assertions": [{"assertion_id": "A1", "subject": "x", "predicate": "improves", "evidence_ids": ["E1"]}], "evidence_ledger": [{"evidence_id": "E1", "status": "author_data"}], "required_assertion_ids": ["A1"]}
        self.assertTrue(mode_fidelity.validate(payload)["valid"])

    def test_negative_polish_changes_number(self):
        payload = polish_payload()
        payload["output_assertions"][0]["value"] = 3.5
        self.assertFalse(mode_fidelity.validate(payload)["valid"])


class ProtocolSelectionTests(unittest.TestCase):
    def test_positive_systematic(self):
        result = protocol.select({"objective": "focused_answer", "comprehensive_search_required": True, "deadline_days": 90, "compatible_effect_estimates_available": False, "declared_protocol": "systematic_review"})
        self.assertTrue(result["valid"])

    def test_boundary_thirty_day_rapid_review(self):
        result = protocol.select({"objective": "time_bound_decision", "comprehensive_search_required": False, "deadline_days": 30, "compatible_effect_estimates_available": False, "declared_protocol": "rapid_review"})
        self.assertEqual(result["selected_protocol"], "rapid_review")
        self.assertTrue(result["valid"])

    def test_negative_incompatible_declared_protocol(self):
        result = protocol.select({"objective": "evidence_mapping", "comprehensive_search_required": False, "deadline_days": None, "compatible_effect_estimates_available": False, "declared_protocol": "meta_analysis"})
        self.assertFalse(result["valid"])


class StatementConsistencyTests(unittest.TestCase):
    def test_positive_public_artifact(self):
        payload = {"inventory": [{"artifact_id": "D1", "supports_claims": True, "access": "public", "persistent_identifier": "https://doi.org/10.1234/data.1", "license": "CC BY 4.0"}], "statement_entries": [{"artifact_id": "D1", "access": "public", "persistent_identifier": "https://doi.org/10.1234/data.1", "license": "CC BY 4.0"}]}
        self.assertTrue(statement.validate(payload)["valid"])

    def test_boundary_restricted_artifact(self):
        row = {"artifact_id": "D1", "supports_claims": True, "access": "restricted", "restriction_reason": "participant consent", "request_route": "data access committee", "decision_authority": "ethics-approved DAC"}
        self.assertTrue(statement.validate({"inventory": [row], "statement_entries": [copy.deepcopy(row)]})["valid"])

    def test_negative_omitted_claim_artifact(self):
        result = statement.validate({"inventory": [{"artifact_id": "D1", "supports_claims": True, "access": "not_available", "reason": "destroyed under approved schedule"}], "statement_entries": []})
        self.assertFalse(result["valid"])
        self.assertEqual(result["omitted_required"], ["D1"])


class FeasibilityTests(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(feasibility.audit(feasibility_payload())["valid"])

    def test_boundary_resource_demand_equal_to_capacity(self):
        result = feasibility.audit(feasibility_payload())
        self.assertEqual(result["resource_overloads"], [])
        self.assertTrue(result["valid"])

    def test_negative_overlap_and_missing_risk(self):
        payload = feasibility_payload()
        payload["work_packages"][1]["start_week"] = 3
        payload["risks"] = []
        self.assertFalse(feasibility.audit(payload)["valid"])


class PatentClaimMapTests(unittest.TestCase):
    def test_positive(self):
        result = claim_map.audit(patent_map_payload())
        self.assertTrue(result["valid"])
        self.assertEqual(result["claim_order"], ["C1", "C2"])

    def test_boundary_single_independent_claim(self):
        payload = patent_map_payload()
        payload["claims"] = payload["claims"][:1]
        self.assertTrue(claim_map.audit(payload)["valid"])

    def test_negative_dependent_claim_adds_no_feature(self):
        payload = patent_map_payload()
        payload["claims"][1]["feature_ids"] = ["F1"]
        self.assertFalse(claim_map.audit(payload)["valid"])


class PriorArtTests(unittest.TestCase):
    def test_positive_separate_date_lanes(self):
        payload = {"critical_date": "2024-01-01", "invention_source_ids": ["INV1"], "candidate_records": [{"record_id": "P1", "source_id": "S1", "publication_date": "2023-01-01", "declared_lane": "prior_art_candidate", "matched_feature_ids": ["F1"]}, {"record_id": "P2", "source_id": "S2", "publication_date": "2025-01-01", "declared_lane": "post_cutoff_background", "matched_feature_ids": []}]}
        self.assertTrue(prior_art.separate(payload)["valid"])

    def test_boundary_unknown_date_remains_unresolved(self):
        payload = {"critical_date": "2024-01-01", "invention_source_ids": ["INV1"], "candidate_records": [{"record_id": "P1", "source_id": "S1", "publication_date": None, "declared_lane": "date_unresolved", "matched_feature_ids": []}]}
        self.assertTrue(prior_art.separate(payload)["valid"])

    def test_negative_invention_source_reused_as_prior_art(self):
        payload = {"critical_date": "2024-01-01", "invention_source_ids": ["INV1"], "candidate_records": [{"record_id": "P1", "source_id": "INV1", "publication_date": "2023-01-01", "declared_lane": "prior_art_candidate", "matched_feature_ids": ["F1"]}]}
        self.assertFalse(prior_art.separate(payload)["valid"])


class ConditionComparisonTests(unittest.TestCase):
    def test_positive_unit_normalization(self):
        result = conditions.compare(condition_records())
        self.assertTrue(result["valid"])
        self.assertEqual(result["comparable_pairs"], 1)

    def test_boundary_different_solvent_is_not_compared(self):
        payload = condition_records()
        payload["records"][1]["conditions"]["solvent"] = "ethanol"
        result = conditions.compare(payload)
        self.assertTrue(result["valid"])
        self.assertFalse(result["pairs"][0]["comparable"])

    def test_negative_unknown_temperature_unit(self):
        payload = condition_records()
        payload["records"][1]["conditions"]["temperature"]["unit"] = "F"
        self.assertFalse(conditions.compare(payload)["valid"])


class IntegrityNeutralityTests(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(integrity.audit(integrity_payload(), "neutral_triage")["valid"])

    def test_boundary_neutral_interpretation(self):
        payload = integrity_payload()
        payload["triage_items"].append({"item_id": "I3", "kind": "interpretation", "text": "The mismatch may be consistent with a second export; intent is not assessed.", "source_ids": ["H1", "N1"]})
        self.assertTrue(integrity.audit(payload, "neutral_triage")["valid"])

    def test_negative_misconduct_verdict(self):
        payload = integrity_payload()
        payload["triage_items"][0]["text"] = "The analyst committed misconduct."
        self.assertFalse(integrity.audit(payload, "neutral_triage")["valid"])


class IntegrityScopeTests(unittest.TestCase):
    def test_positive_high_scope(self):
        self.assertTrue(integrity.audit(integrity_payload(), "scope_severity")["valid"])

    def test_boundary_immediate_danger_is_critical(self):
        payload = integrity_payload()
        payload["signals"]["data_security_breach"] = False
        payload["signals"]["immediate_participant_danger"] = True
        payload["declared_severity"] = "critical"
        payload["declared_scope"] = ["provenance", "ethics"]
        result = integrity.audit(payload, "scope_severity")
        self.assertTrue(result["valid"])
        self.assertEqual(result["details"]["scope_severity"]["computed_severity"], "critical")

    def test_negative_understated_severity(self):
        payload = integrity_payload()
        payload["declared_severity"] = "low"
        self.assertFalse(integrity.audit(payload, "scope_severity")["valid"])


class IntegrityRemediationTests(unittest.TestCase):
    def test_positive_high_escalation(self):
        self.assertTrue(integrity.audit(integrity_payload(), "remediation_escalation")["valid"])

    def test_boundary_low_severity_uses_minimal_actions(self):
        payload = integrity_payload()
        payload["signals"] = {key: False for key in payload["signals"]}
        payload["proposed_actions"] = [{"action": "preserve_evidence"}, {"action": "restrict_access"}]
        self.assertTrue(integrity.audit(payload, "remediation_escalation")["valid"])

    def test_negative_destructive_action_and_missing_referral(self):
        payload = integrity_payload()
        payload["proposed_actions"] = [{"action": "delete_originals"}]
        self.assertFalse(integrity.audit(payload, "remediation_escalation")["valid"])


if __name__ == "__main__":
    unittest.main()
