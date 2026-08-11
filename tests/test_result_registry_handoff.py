from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResultRegistryHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry_module = load_module(
            "registry_handoff_registry",
            "skills/zju-statistics-audit/scripts/reconcile_result_registry.py",
        )
        cls.renderer_module = load_module(
            "registry_handoff_renderer",
            "skills/zju-statistics-audit/scripts/render_result_tokens.py",
        )
        cls.handoff_module = load_module(
            "registry_handoff_validator",
            "skills/zju-statistics-audit/scripts/validate_result_handoff.py",
        )

    def contract(self):
        return {
            "contract_id": "AC-1",
            "study_id": "STUDY-1",
            "question": "Does treatment change Y?",
            "design_stage": "frozen",
            "design": {
                "experimental_unit": "animal",
                "observational_unit": "measurement",
                "assignment": "randomized",
                "randomization_unit": "animal",
                "grouping_structure": "animal within batch",
            },
            "outcomes": [
                {"outcome_id": "OUT-Y14", "role": "primary", "variable": "Y", "scale": "continuous", "timepoint": "day 14"},
                {"outcome_id": "OUT-QC", "role": "quality_control", "variable": "QC", "scale": "continuous", "timepoint": "run"},
            ],
            "analyses": [
                {
                    "analysis_id": "AN-PRIMARY",
                    "outcome_ids": ["OUT-Y14"],
                    "estimand": "day-14 mean difference",
                    "analysis_population": "all randomized animals with observed outcome",
                    "model_family": "linear model",
                    "effect_measure": "mean difference",
                    "uncertainty": "95% CI",
                    "missing_data_strategy": "likelihood under MAR",
                    "multiplicity_family": "single primary contrast",
                    "diagnostics": ["residual checks"],
                    "sensitivity_analyses": ["robust regression"],
                }
            ],
        }

    def registry(self):
        return {
            "schema_version": "1.0",
            "study_id": "STUDY-1",
            "analysis_contract_id": "AC-1",
            "registry_version": "2026-08-11.1",
            "results": [
                {
                    "result_id": "RES-Y14",
                    "analysis_id": "AN-PRIMARY",
                    "outcome_id": "OUT-Y14",
                    "result_kind": "inferential",
                    "analysis_population": "all randomized animals with observed outcome",
                    "effect_measure": "mean difference",
                    "estimate": -2.43,
                    "unit": "mg/L",
                    "direction": "lower_in_treatment",
                    "ci": {"level": 0.95, "lower": -3.71, "upper": -1.12},
                    "p_value": {"operator": "<", "value": 0.0015},
                    "multiplicity_status": "single primary contrast",
                    "n": {"experimental_units": 48, "observations": 48},
                    "diagnostics": [{"check": "residual_pattern", "result": "acceptable"}],
                    "sensitivity_analyses": [{"analysis_id": "AN-ROBUST", "conclusion": "stable"}],
                    "source_anchor": "analysis/output.json#/primary",
                    "status": "verified",
                }
            ],
            "uses": [],
        }

    def test_registry_resolves_analysis_outcomes_and_bounded_p_values(self):
        registry = self.registry()
        self.assertTrue(self.registry_module.validate(registry, self.contract())["valid"])

        unknown_analysis = copy.deepcopy(registry)
        unknown_analysis["results"][0]["analysis_id"] = "AN-UNKNOWN"
        self.assertFalse(self.registry_module.validate(unknown_analysis, self.contract())["valid"])

        wrong_outcome = copy.deepcopy(registry)
        wrong_outcome["results"][0]["outcome_id"] = "OUT-QC"
        self.assertFalse(self.registry_module.validate(wrong_outcome, self.contract())["valid"])

        invalid_bound = copy.deepcopy(registry)
        invalid_bound["results"][0]["p_value"] = {"operator": "approximately", "value": 0.001}
        self.assertFalse(self.registry_module.validate(invalid_bound, self.contract())["valid"])

    def test_renderer_emits_reconcilable_uses_with_rounding(self):
        registry = self.registry()
        rendered = self.renderer_module.render(
            "Difference {{result:RES-Y14:estimate|.1f}} mg/L; p{{result:RES-Y14:p_value|.3f}}; n={{result:RES-Y14:n.experimental_units}}.",
            registry,
            consumer_type="figure",
            anchor="Fig. 2a legend",
            use_id_prefix="FIG2A",
        )
        self.assertTrue(rendered["valid"], rendered)
        self.assertEqual(rendered["text"], "Difference -2.4 mg/L; p<0.002; n=48.")
        self.assertEqual(len(rendered["uses"]), 3)
        for use in rendered["uses"]:
            self.assertEqual(set(use), {"use_id", "consumer_type", "anchor", "result_id", "values"})

        registry["uses"] = rendered["uses"]
        self.assertTrue(self.registry_module.validate(registry, self.contract())["valid"])

        tampered = copy.deepcopy(registry)
        tampered["uses"][0]["values"]["estimate"]["rendered"] = "2.4"
        self.assertFalse(self.registry_module.validate(tampered, self.contract())["valid"])

    def complete_handoff(self):
        registry = self.registry()
        rendered = self.renderer_module.render(
            "The estimate was {{result:RES-Y14:estimate|.2f}} mg/L.",
            registry,
            consumer_type="results",
            anchor="Results paragraph 2",
            use_id_prefix="RESULTS-P2",
        )
        registry["uses"] = rendered["uses"]
        return {
            "analysis_contract": self.contract(),
            "result_registry": registry,
            "evidence": [{"evidence_id": "EV-RAW"}],
            "claims": [
                {
                    "claim_id": "CLM-Y14",
                    "claim_type": "author_result",
                    "status": "supported",
                    "evidence_ids": ["EV-RAW"],
                    "result_ids": ["RES-Y14"],
                }
            ],
            "writing_contract": {
                "results_sections": [
                    {"section_id": "RESULTS-2", "claim_ids": ["CLM-Y14"], "evidence_ids": ["EV-RAW"], "result_ids": ["RES-Y14"]}
                ]
            },
            "figure_manifest": {
                "figure_id": "FIG-2",
                "route": "data_figure",
                "panels": [
                    {"panel_id": "A", "analysis_ids": ["AN-PRIMARY"], "result_ids": ["RES-Y14"], "claim_ids": ["CLM-Y14"], "evidence_ids": ["EV-RAW"]}
                ],
            },
            "review_report": {
                "concerns": [
                    {"concern_id": "R1-M1", "claim_ids": ["CLM-Y14"], "evidence_ids": ["EV-RAW"], "result_ids": ["RES-Y14"]}
                ]
            },
            "data_inventory": {
                "artifacts": [
                    {"artifact_id": "ART-CODE", "supports_claims": ["CLM-Y14"], "supports_results": ["RES-Y14"]},
                    {"artifact_id": "ART-ENV", "supports_claims": ["CLM-Y14"], "supports_results": ["RES-Y14"]},
                ],
                "reproducibility_packages": [
                    {"package_id": "RP-1", "result_ids": ["RES-Y14"], "artifact_ids": ["ART-CODE", "ART-ENV"], "expected_outputs": ["RES-Y14"]}
                ],
            },
        }

    def test_registry_aware_handoff_accepts_all_consumers(self):
        report = self.handoff_module.validate(self.complete_handoff())
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["results"], 1)
        self.assertEqual(report["panels"], 1)

    def test_registry_aware_handoff_rejects_dangling_consumer_ids(self):
        mutations = (
            ("writing", lambda bundle: bundle["writing_contract"]["results_sections"][0].update({"result_ids": ["RES-UNKNOWN"]})),
            ("figure", lambda bundle: bundle["figure_manifest"]["panels"][0].update({"analysis_ids": ["AN-UNKNOWN"]})),
            ("review", lambda bundle: bundle["review_report"]["concerns"][0].update({"evidence_ids": ["EV-UNKNOWN"]})),
            ("data", lambda bundle: bundle["data_inventory"]["artifacts"][0].update({"supports_results": ["RES-UNKNOWN"]})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                bundle = self.complete_handoff()
                mutate(bundle)
                self.assertFalse(self.handoff_module.validate(bundle)["valid"])


if __name__ == "__main__":
    unittest.main()
