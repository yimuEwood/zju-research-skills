from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def hypothesis_plan() -> dict:
    return {
        "schema_version": "2.0",
        "known_evidence_ids": ["EV-1", "EV-2"],
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "mechanism": "The intervention changes Y through mediator M",
                "assumptions": ["The assay resolves M"],
                "evidence_ids": ["EV-1"],
                "contradicting_evidence_ids": [],
                "unique_predictions": ["Y and M increase together"],
                "falsifiers": ["Y increases while M is unchanged"],
                "boundary_conditions": ["tested system and time point"],
                "alternative_ids": ["H0"],
            },
            {
                "hypothesis_id": "H0",
                "mechanism": "The intervention does not act through M",
                "assumptions": ["The control captures background drift"],
                "evidence_ids": ["EV-2"],
                "contradicting_evidence_ids": [],
                "unique_predictions": ["M remains unchanged"],
                "falsifiers": ["M changes reproducibly under intervention"],
                "boundary_conditions": ["tested system and time point"],
                "alternative_ids": ["H1"],
            },
        ],
        "experiments": [{
            "experiment_id": "X-DISC-1",
            "hypothesis_ids": ["H1", "H0"],
            "experimental_unit": "independently prepared sample",
            "intervention": "apply perturbation P",
            "control": "matched vehicle control",
            "primary_outcome": "OUT-Y-M-JOINT",
            "decision_rule": "compare the prespecified joint Y/M pattern",
            "predicted_outcomes": {"H1": "Y_up_and_M_up", "H0": "M_unchanged"},
            "inconclusive_region": "confidence region overlaps both prospective signatures",
            "feasibility": {"technical": 0.9, "sample_access": 0.8},
            "cost_level": 2,
            "time_level": 2,
            "orthogonal_measurement": True,
            "capability_ids": ["CAP-X1"],
        }],
    }


class ExecutionHandoffTests(unittest.TestCase):
    def test_hypothesis_plan_flows_to_proposal_and_schema_v2_log(self):
        hypothesis = load_module(
            "hypothesis_execution_handoff",
            "skills/zju-hypothesis-design/scripts/validate_hypothesis_set.py",
        )
        proposal = load_module(
            "proposal_execution_handoff",
            "skills/zju-proposal-writer/scripts/validate_proposal_manifest.py",
        )
        log = load_module(
            "experiment_log_execution_handoff",
            "skills/zju-experiment-log/scripts/build_log.py",
        )
        source_plan = hypothesis_plan()
        self.assertTrue(hypothesis.validate(source_plan)["valid"], hypothesis.validate(source_plan))
        handoff = {
            "proposal_id": "PROP-HYP-1",
            "hypothesis_plan": source_plan,
            "scheme_status": "template_pending",
            "schedule_by_experiment": {"X-DISC-1": {"start_month": 1, "end_month": 4}},
            "method_by_experiment": {"X-DISC-1": ["prospective controlled perturbation"]},
            "default_owner": "experimental WP lead",
            "capabilities": [{
                "capability_id": "CAP-X1",
                "item": "assay and sample access for X-DISC-1",
                "dimension": "method",
                "status": "available",
                "evidence_ids": ["EV-1"],
                "constraint": "",
                "mitigation": "",
            }],
            "risks": [{"risk_id": "R-ASSAY"}],
            "compliance": [{"item": "scheme template pending"}],
            "submission_ready": False,
        }
        compiled = proposal.compile_handoff(handoff)
        self.assertTrue(compiled["execution_plan_ready"], compiled)
        wp = compiled["manifest"]["work_packages"][0]
        for field in (
            "intervention", "control", "experimental_unit", "primary_outcome",
            "predicted_outcomes", "inconclusive_region",
        ):
            self.assertEqual(wp[field], source_plan["experiments"][0][field])
        self.assertEqual(compiled["manifest"]["hypotheses"][0]["mechanism"], source_plan["hypotheses"][0]["mechanism"])
        self.assertEqual(compiled["manifest"]["hypotheses"][0]["predictions"], source_plan["hypotheses"][0]["unique_predictions"])
        self.assertEqual(compiled["milestone_decision_tree"][0]["branches"]["inconclusive"]["condition"], source_plan["experiments"][0]["inconclusive_region"])

        log_intake = {
            "schema_version": "2.0",
            "experiment_id": source_plan["experiments"][0]["experiment_id"],
            "study_id": "STUDY-HYP-1",
            "title": "Discriminating experiment H1 versus H0",
            "project": "PROP-HYP-1",
            "operator": "researcher",
            "design_stage": "frozen",
            "experimental_unit": wp["experimental_unit"],
            "observational_unit": "assay readout",
            "repeats": {
                "biological_replicate_definition": "independent sample preparation",
                "technical_replicate_definition": "repeat assay well",
                "technical_replicate_aggregation": "mean within experimental unit before inference",
            },
            "randomization": {
                "assignment": "randomized",
                "randomization_unit": "independently prepared sample",
                "method": "computer-generated allocation sequence",
            },
            "hierarchy": {
                "group_structure": "assay wells nested within independently prepared samples",
                "blocking_or_nesting": "measurement batch recorded as a block",
            },
            "repeated_measures": False,
            "outcome_ids": [wp["primary_outcome"]],
            "data_lineage": [
                {"artifact_id": "RAW-X1", "role": "raw_data", "derived_from": []},
                {"artifact_id": "QC-X1", "role": "qc", "derived_from": ["RAW-X1"]},
            ],
            "analysis_contract": {
                "contract_id": "AC-HYP-1",
                "status": "frozen",
                "path": "analysis-contract.json",
                "sha256": "a" * 64,
            },
            "hypothesis_ids": wp["hypothesis_ids"],
            "intervention": wp["intervention"],
            "control": wp["control"],
            "primary_outcome": wp["primary_outcome"],
            "predicted_outcomes": wp["predicted_outcomes"],
            "inconclusive_region": wp["inconclusive_region"],
            "objective": compiled["manifest"]["objectives"][0]["question"],
        }
        rendered = log.render(log_intake)
        frontmatter = yaml.safe_load(rendered.split("---", 2)[1])
        self.assertEqual(frontmatter["schema_version"], "2.0")
        self.assertEqual(frontmatter["experimental_unit"], wp["experimental_unit"])
        self.assertEqual(frontmatter["predicted_outcomes"], wp["predicted_outcomes"])
        self.assertEqual(frontmatter["data_lineage"][1]["derived_from"], ["RAW-X1"])
        self.assertEqual(frontmatter["analysis_contract"]["contract_id"], "AC-HYP-1")
        self.assertIn("## Design and analysis handoff", rendered)

        broken_plan = hypothesis_plan()
        broken_plan["experiments"][0]["primary_outcome"] = ""
        broken = proposal.compile_handoff({**handoff, "hypothesis_plan": broken_plan})
        self.assertFalse(broken["execution_plan_ready"])
        broken_log = dict(log_intake)
        broken_log["observational_unit"] = ""
        with self.assertRaisesRegex(ValueError, "observational_unit"):
            log.render(broken_log)

    def test_patent_accepts_nested_result_registry_and_artifact_inventory(self):
        patent = load_module(
            "patent_nested_research_outputs",
            "skills/zju-paper-to-patent/scripts/validate_feature_ledger.py",
        )
        packet = {
            "workflow_version": "2.0",
            "sources": [
                {"source_id": "P1", "source_type": "paper"},
                {"source_id": "X1", "source_type": "experiment"},
            ],
            "result_registry": {"schema_version": "1.0", "results": [
                {"result_id": "RES-EFFECT", "outcome_id": "OUT-1"},
                {"result_id": "RES-DISCRIM", "outcome_id": "OUT-2"},
            ]},
            "artifact_inventory": {"artifacts": [
                {"artifact_id": "ART-ALT", "role": "analysis_output"},
            ]},
            "features": [
                {"feature_id": "FT-1", "normalized_term": "barrier", "description": "barrier between layers", "source_ids": ["P1"], "source_anchor": "p. 4, lines 2-5", "support_state": "explicit", "claim_role": "independent", "confidentiality": "public"},
                {"feature_id": "FT-2", "normalized_term": "material A", "description": "barrier comprises material A", "source_ids": ["P1"], "source_anchor": "p. 4, lines 6-8", "support_state": "explicit", "claim_role": "dependent", "confidentiality": "public"},
                {"feature_id": "FT-3", "normalized_term": "material B", "description": "barrier comprises material B", "source_ids": ["X1"], "source_anchor": "experiment X1, record 4", "support_state": "explicit", "claim_role": "embodiment", "confidentiality": "unpublished"},
            ],
            "technical_concepts": [{
                "concept_id": "IC-1", "problem": "interface degradation",
                "solution_feature_ids": ["FT-1", "FT-2"], "technical_effect": "bounded stability improvement",
                "evidence_ids": ["RES-EFFECT"], "discriminating_evidence_ids": ["RES-DISCRIM"],
            }],
            "claim_candidates": [
                {"claim_id": "CL-1", "claim_role": "independent", "parent_claim_ids": [], "concept_ids": ["IC-1"], "feature_ids": ["FT-1"]},
                {"claim_id": "CL-2", "claim_role": "dependent", "parent_claim_ids": ["CL-1"], "concept_ids": ["IC-1"], "feature_ids": ["FT-2"]},
            ],
            "prior_art_terms": [{
                "query_id": "Q-1", "concept_ids": ["IC-1"], "target_feature_ids": ["FT-1", "FT-2"],
                "problem_terms": ["interface degradation"], "solution_terms": ["barrier layer"], "effect_terms": ["stability"],
                "date_cutoff": "2026-08-11", "target_sources": ["patent database"],
            }],
            "alternative_embodiments": [{
                "embodiment_id": "EB-1", "concept_id": "IC-1", "replaces_feature_ids": ["FT-2"],
                "alternative_feature_ids": ["FT-3"], "target_effect": "interface stability", "support_state": "explicit",
                "evidence_ids": ["ART-ALT"], "discriminating_evidence_ids": ["RES-DISCRIM"],
            }],
        }
        report = patent.validate(packet)
        self.assertTrue(report["invention_map_ready"], report)
        self.assertEqual(report["problem_solution_effect_map"][0]["evidence_ids"], ["RES-EFFECT"])

        missing_registry_result = copy.deepcopy(packet)
        missing_registry_result["result_registry"]["results"] = []
        invalid = patent.validate(missing_registry_result)
        self.assertFalse(invalid["invention_map_ready"])
        self.assertTrue(any("RES-EFFECT" in row["message"] for row in invalid["findings"]))


if __name__ == "__main__":
    unittest.main()
