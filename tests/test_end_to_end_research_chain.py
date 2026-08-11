from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence_row(record_id: str, study_id: str, role: str, system: str) -> dict[str, object]:
    return {
        "record_id": record_id,
        "study_id": study_id,
        "report_id": f"REPORT-{study_id}",
        "citation_id": f"10.1000/{study_id.lower()}",
        "design": "controlled experiment",
        "population_or_system": system,
        "sample_and_unit": "12 independent preparations",
        "intervention_or_exposure": "perturbation P",
        "comparator": "matched vehicle",
        "outcome": "joint Y/M response",
        "time_point": "day 7",
        "effect_estimate": "reported",
        "uncertainty": "reported interval",
        "risk_of_bias": "some_concerns",
        "source_anchor": "p. 4, Fig. 2",
        "claim_id": "CLM-MEDIATION",
        "evidence_role": role,
        "directness": "direct",
    }


class EndToEndResearchChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normalizer = load_module(
            "chain_normalizer", "skills/zju-literature-search/scripts/normalize_records.py"
        )
        cls.evidence = load_module(
            "chain_evidence", "skills/zju-evidence-synthesis/scripts/validate_evidence_table.py"
        )
        cls.conflicts = load_module(
            "chain_conflicts", "skills/zju-evidence-synthesis/scripts/build_conflict_matrix.py"
        )
        cls.hypotheses = load_module(
            "chain_hypotheses", "skills/zju-hypothesis-design/scripts/validate_hypothesis_set.py"
        )
        cls.proposal = load_module(
            "chain_proposal", "skills/zju-proposal-writer/scripts/validate_proposal_manifest.py"
        )
        cls.experiment_log = load_module(
            "chain_experiment_log", "skills/zju-experiment-log/scripts/build_log.py"
        )
        cls.analysis = load_module(
            "chain_analysis", "skills/zju-statistics-audit/scripts/validate_analysis_contract.py"
        )
        cls.registry = load_module(
            "chain_registry", "skills/zju-statistics-audit/scripts/reconcile_result_registry.py"
        )
        cls.renderer = load_module(
            "chain_renderer", "skills/zju-statistics-audit/scripts/render_result_tokens.py"
        )
        cls.handoff = load_module(
            "chain_handoff", "skills/zju-statistics-audit/scripts/validate_result_handoff.py"
        )

    def test_discovery_to_publication_chain_preserves_ids_and_decisions(self):
        records = self.normalizer.deduplicate([
            {"record_id": "REC-SUPPORT", "doi": "10.1000/support", "title": "Mediator response"},
            {"record_id": "REC-CONTRADICT", "doi": "10.1000/contradict", "title": "Boundary response"},
        ])
        record_ids = {row["record_id"] for row in records}
        self.assertEqual(record_ids, {"REC-SUPPORT", "REC-CONTRADICT"})

        rows = [
            evidence_row("REC-SUPPORT", "S-SUPPORT", "supports", "system A"),
            evidence_row("REC-CONTRADICT", "S-CONTRADICT", "contradicts", "system B"),
        ]
        evidence_bundle = {
            "rows": rows,
            "claims": [{
                "claim_id": "CLM-MEDIATION",
                "certainty": "low",
                "supporting_study_ids": ["S-SUPPORT"],
                "contradicting_study_ids": ["S-CONTRADICT"],
                "contextual_study_ids": [],
            }],
            "conflicts": [{
                "conflict_id": "CF-MEDIATION",
                "claim_id": "CLM-MEDIATION",
                "study_ids": ["S-SUPPORT", "S-CONTRADICT"],
            }],
        }
        evidence_report = self.evidence.validate(evidence_bundle)
        self.assertTrue(evidence_report["valid"], evidence_report)
        conflict_report = self.conflicts.build(rows)
        self.assertEqual(conflict_report["summary"]["directional_conflicts"], 1)
        self.assertEqual(conflict_report["groups"][0]["resolution_state"], "context_hypothesis_available")

        hypothesis_plan = {
            "schema_version": "2.0",
            "evidence_map": {
                "rows": [
                    {"evidence_id": "EV-SUPPORT", "record_id": "REC-SUPPORT"},
                    {"evidence_id": "EV-CONTRADICT", "record_id": "REC-CONTRADICT"},
                ],
                "claims": evidence_bundle["claims"],
                "gaps": [{"gap_id": "GAP-CONTEXT", "claim_id": "CLM-MEDIATION"}],
            },
            "hypotheses": [
                {
                    "hypothesis_id": "H-M",
                    "mechanism": "P changes Y through mediator M in system A",
                    "assumptions": ["the M assay is specific"],
                    "evidence_ids": ["EV-SUPPORT"],
                    "contradicting_evidence_ids": ["EV-CONTRADICT"],
                    "unique_predictions": ["Y and M increase together"],
                    "falsifiers": ["Y changes while M remains stable"],
                    "boundary_conditions": ["system A"],
                    "alternative_ids": ["H-ALT"],
                },
                {
                    "hypothesis_id": "H-ALT",
                    "mechanism": "P changes Y independently of M",
                    "assumptions": ["vehicle captures background drift"],
                    "evidence_ids": ["EV-CONTRADICT"],
                    "contradicting_evidence_ids": ["EV-SUPPORT"],
                    "unique_predictions": ["Y changes without M"],
                    "falsifiers": ["M tracks Y under orthogonal measurement"],
                    "boundary_conditions": ["system B"],
                    "alternative_ids": ["H-M"],
                },
            ],
            "experiments": [{
                "experiment_id": "EXP-DISCRIM",
                "hypothesis_ids": ["H-M", "H-ALT"],
                "experimental_unit": "independent preparation",
                "intervention": "perturbation P",
                "control": "matched vehicle",
                "primary_outcome": "OUT-YM",
                "decision_rule": "compare the prespecified joint Y/M signature",
                "predicted_outcomes": {"H-M": "Y_up_M_up", "H-ALT": "Y_up_M_stable"},
                "inconclusive_region": "confidence region overlaps both signatures",
                "feasibility": {"technical": 0.9, "sample_access": 0.8},
                "cost_level": 2,
                "time_level": 2,
                "orthogonal_measurement": True,
                "capability_ids": ["CAP-ASSAY"],
            }],
        }
        hypothesis_report = self.hypotheses.validate(hypothesis_plan)
        self.assertTrue(hypothesis_report["valid"], hypothesis_report)
        self.assertEqual(hypothesis_report["experiment_discrimination_ranking"][0]["distinguished_pairs"], 1)

        proposal_report = self.proposal.compile_handoff({
            "proposal_id": "PROP-CHAIN",
            "hypothesis_plan": hypothesis_plan,
            "scheme_status": "template_pending",
            "schedule_by_experiment": {"EXP-DISCRIM": {"start_month": 1, "end_month": 3}},
            "method_by_experiment": {"EXP-DISCRIM": ["prospective controlled perturbation"]},
            "default_owner": "experimental lead",
            "capabilities": [{
                "capability_id": "CAP-ASSAY",
                "item": "orthogonal Y and M assays",
                "dimension": "method",
                "status": "available",
                "evidence_ids": ["EV-SUPPORT"],
                "constraint": "",
                "mitigation": "",
            }],
            "risks": [{"risk_id": "R-ASSAY"}],
            "compliance": [{"item": "template pending"}],
        })
        self.assertTrue(proposal_report["execution_plan_ready"], proposal_report)
        work_package = proposal_report["manifest"]["work_packages"][0]
        self.assertEqual(work_package["primary_outcome"], "OUT-YM")
        self.assertEqual(work_package["predicted_outcomes"], hypothesis_plan["experiments"][0]["predicted_outcomes"])

        analysis_contract = {
            "contract_id": "AC-CHAIN",
            "study_id": "STUDY-CHAIN",
            "question": proposal_report["manifest"]["objectives"][0]["question"],
            "design_stage": "frozen",
            "design": {
                "experimental_unit": work_package["experimental_unit"],
                "observational_unit": "assay readout",
                "assignment": "randomized",
                "randomization_unit": work_package["experimental_unit"],
                "grouping_structure": "readouts within preparations",
            },
            "outcomes": [{
                "outcome_id": "OUT-YM",
                "role": "primary",
                "variable": "joint Y/M signature",
                "scale": "continuous",
                "timepoint": "day 7",
            }],
            "analyses": [{
                "analysis_id": "AN-YM",
                "outcome_ids": ["OUT-YM"],
                "estimand": "joint-signature contrast",
                "analysis_population": "all randomized preparations with observed outcomes",
                "model_family": "multivariate linear model",
                "effect_measure": "standardized contrast",
                "uncertainty": "95% confidence interval",
                "missing_data_strategy": "likelihood under MAR",
                "multiplicity_family": "single primary contrast",
                "diagnostics": ["residual and covariance checks"],
                "sensitivity_analyses": ["robust covariance estimate"],
            }],
        }
        analysis_report = self.analysis.validate(analysis_contract)
        self.assertTrue(analysis_report["plan_ready"], analysis_report)

        rendered_log = self.experiment_log.render({
            "schema_version": "2.0",
            "experiment_id": "EXP-DISCRIM",
            "study_id": "STUDY-CHAIN",
            "title": "Discriminate M-mediated and alternative mechanisms",
            "project": "PROP-CHAIN",
            "operator": "researcher",
            "design_stage": "frozen",
            "experimental_unit": work_package["experimental_unit"],
            "observational_unit": "assay readout",
            "repeats": {
                "biological_replicate_definition": "independent preparation",
                "technical_replicate_definition": "repeat assay well",
                "technical_replicate_aggregation": "mean within preparation",
            },
            "randomization": {
                "assignment": "randomized",
                "randomization_unit": work_package["experimental_unit"],
                "method": "computer-generated sequence",
            },
            "hierarchy": {
                "group_structure": "assay wells within preparations",
                "blocking_or_nesting": "batch recorded as a block",
            },
            "repeated_measures": False,
            "outcome_ids": ["OUT-YM"],
            "data_lineage": [{"artifact_id": "RAW-CHAIN", "role": "raw_data", "derived_from": []}],
            "analysis_contract": {
                "contract_id": "AC-CHAIN",
                "status": "frozen",
                "path": "analysis-contract.json",
                "sha256": "a" * 64,
            },
            "hypothesis_ids": work_package["hypothesis_ids"],
            "intervention": work_package["intervention"],
            "control": work_package["control"],
            "primary_outcome": work_package["primary_outcome"],
            "predicted_outcomes": work_package["predicted_outcomes"],
            "inconclusive_region": work_package["inconclusive_region"],
        })
        log_frontmatter = yaml.safe_load(rendered_log.split("---", 2)[1])
        self.assertEqual(log_frontmatter["analysis_contract"]["contract_id"], "AC-CHAIN")
        self.assertEqual(log_frontmatter["outcome_ids"], ["OUT-YM"])

        registry = {
            "schema_version": "1.0",
            "study_id": "STUDY-CHAIN",
            "analysis_contract_id": "AC-CHAIN",
            "registry_version": "2026-08-11.1",
            "results": [{
                "result_id": "RES-YM",
                "analysis_id": "AN-YM",
                "outcome_id": "OUT-YM",
                "result_kind": "inferential",
                "analysis_population": "all randomized preparations with observed outcomes",
                "effect_measure": "standardized contrast",
                "estimate": 1.24,
                "unit": "SD",
                "direction": "supports_H-M",
                "ci": {"level": 0.95, "lower": 0.40, "upper": 2.08},
                "p_value": {"operator": "<", "value": 0.01},
                "multiplicity_status": "single primary contrast",
                "n": {"experimental_units": 24, "observations": 48},
                "diagnostics": [{"check": "residuals", "result": "acceptable"}],
                "sensitivity_analyses": [{"analysis_id": "AN-YM-ROBUST", "conclusion": "stable"}],
                "source_anchor": "analysis/output.json#/ym",
                "status": "verified",
            }],
            "uses": [],
        }
        token_report = self.renderer.render(
            "The standardized contrast was {{result:RES-YM:estimate|.1f}} SD; p{{result:RES-YM:p_value|.2f}}.",
            registry,
            consumer_type="results",
            anchor="Results paragraph 2",
            use_id_prefix="RESULTS-P2",
        )
        self.assertTrue(token_report["valid"], token_report)
        registry["uses"] = token_report["uses"]
        self.assertTrue(self.registry.validate(registry, analysis_contract)["valid"])

        consumer_bundle = {
            "analysis_contract": analysis_contract,
            "result_registry": registry,
            "evidence": [{"evidence_id": "EV-SUPPORT"}, {"evidence_id": "EV-CONTRADICT"}],
            "claims": [{
                "claim_id": "CLM-RESULT",
                "claim_type": "author_result",
                "status": "supported",
                "evidence_ids": ["EV-SUPPORT"],
                "result_ids": ["RES-YM"],
            }],
            "writing_contract": {"results_sections": [{
                "section_id": "RESULTS-2",
                "claim_ids": ["CLM-RESULT"],
                "evidence_ids": ["EV-SUPPORT"],
                "result_ids": ["RES-YM"],
            }]},
            "figure_manifest": {
                "figure_id": "FIG-2",
                "route": "data_figure",
                "panels": [{
                    "panel_id": "A",
                    "analysis_ids": ["AN-YM"],
                    "result_ids": ["RES-YM"],
                    "claim_ids": ["CLM-RESULT"],
                    "evidence_ids": ["EV-SUPPORT"],
                }],
            },
            "review_report": {"concerns": [{
                "concern_id": "R1-M1",
                "claim_ids": ["CLM-RESULT"],
                "evidence_ids": ["EV-CONTRADICT"],
                "result_ids": ["RES-YM"],
            }]},
            "data_inventory": {
                "artifacts": [
                    {"artifact_id": "ART-CODE", "supports_claims": ["CLM-RESULT"], "supports_results": ["RES-YM"]},
                    {"artifact_id": "ART-ENV", "supports_claims": ["CLM-RESULT"], "supports_results": ["RES-YM"]},
                ],
                "reproducibility_packages": [{
                    "package_id": "RP-1",
                    "result_ids": ["RES-YM"],
                    "artifact_ids": ["ART-CODE", "ART-ENV"],
                    "expected_outputs": ["RES-YM"],
                }],
            },
        }
        handoff_report = self.handoff.validate(consumer_bundle)
        self.assertTrue(handoff_report["valid"], handoff_report)

        broken = copy.deepcopy(consumer_bundle)
        broken["figure_manifest"]["panels"][0]["analysis_ids"] = ["AN-NOT-IN-CONTRACT"]
        self.assertFalse(self.handoff.validate(broken)["valid"])


if __name__ == "__main__":
    unittest.main()
