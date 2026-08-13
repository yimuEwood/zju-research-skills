from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DistilledExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pooler = load_module("pool_effects", "skills/zju-evidence-synthesis/scripts/pool_effects.py")
        cls.release = load_module("build_release_manifest", "skills/zju-data-availability/scripts/build_release_manifest.py")
        cls.portfolio = load_module("select_experiment_portfolio", "skills/zju-hypothesis-design/scripts/select_experiment_portfolio.py")

    @staticmethod
    def pool_payload(estimates=(1.0, 2.0), standard_errors=(0.5, 1.0)):
        effects = []
        for index, (estimate, standard_error) in enumerate(zip(estimates, standard_errors), 1):
            effects.append({
                "study_id": f"S{index}", "effect_measure": "mean_difference",
                "estimand": "treatment minus control", "outcome": "yield", "time_point": "day_28",
                "analysis_scale": "identity", "effect_estimate": estimate, "standard_error": standard_error,
            })
        return {
            "effect_measure": "mean_difference", "estimand": "treatment minus control",
            "outcome": "yield", "time_point": "day_28", "analysis_scale": "identity",
            "independent_estimates": True, "confidence_level": 0.95,
            "tau_squared_estimator": "reml", "random_effects_inference": "hartung_knapp_modified",
            "effects": effects,
        }

    @staticmethod
    def availability_payload(artifact):
        return {
            "workflow_version": "2.0", "computational_results_present": False,
            "artifacts": [artifact], "reproducibility_packages": [],
        }

    @staticmethod
    def hypotheses():
        result = []
        for hypothesis_id in ("H1", "H2", "H3"):
            result.append({
                "hypothesis_id": hypothesis_id, "mechanism": f"mechanism {hypothesis_id}",
                "assumptions": ["assumption"], "evidence_ids": ["E1"], "contradicting_evidence_ids": [],
                "unique_predictions": ["prediction 1", "prediction 2"],
                "falsifiers": ["falsifier 1", "falsifier 2"],
                "alternative_ids": [item for item in ("H1", "H2", "H3") if item != hypothesis_id],
                "boundary_conditions": ["measured domain"],
            })
        return result

    @staticmethod
    def experiment(experiment_id, linked, cost, **extra):
        item = {
            "experiment_id": experiment_id, "hypothesis_ids": linked, "experimental_unit": "sample",
            "intervention": "prespecified perturbation", "control": "vehicle", "primary_outcome": "signal",
            "decision_rule": "compare prespecified signatures",
            "predicted_outcomes": {value: f"signature-{value}" for value in linked},
            "inconclusive_region": "within measurement error", "feasibility": 0.8, "cost_level": 1,
            "time_level": 1, "orthogonal_measurement": False, "cost": cost,
            "eligible": True, "feasibility_status": "eligible", "ethics_status": "not_required",
        }
        item.update(extra)
        return item

    def plan(self, experiments, **extra):
        payload = {
            "schema_version": "2.0", "known_evidence_ids": ["E1"],
            "cost_unit": "thousand_cny", "hypotheses": self.hypotheses(), "experiments": experiments,
        }
        payload.update(extra)
        return payload

    def test_inverse_variance_pooling_matches_hand_calculation(self):
        result = self.pooler.pool(self.pool_payload())
        self.assertAlmostEqual(result["fixed_effect"]["estimate"], 1.2, places=12)
        self.assertAlmostEqual(result["fixed_effect"]["standard_error"], math.sqrt(0.2), places=12)
        self.assertEqual(result["study_ids"], ["S1", "S2"])

    def test_pooling_rejects_incompatible_estimands(self):
        payload = self.pool_payload()
        payload["effects"][1]["estimand"] = "per protocol"
        with self.assertRaisesRegex(ValueError, "incompatible estimand"):
            self.pooler.pool(payload)

    def test_ratio_pooling_requires_log_scale_and_back_transforms(self):
        payload = {
            "effect_measure": "risk_ratio", "estimand": "intention_to_treat", "outcome": "response",
            "time_point": "week_8", "analysis_scale": "log", "independent_estimates": True,
            "tau_squared_estimator": "reml", "random_effects_inference": "hartung_knapp_modified",
            "effects": [
                {"study_id": "S1", "effect_measure": "risk_ratio", "estimand": "intention_to_treat", "outcome": "response", "time_point": "week_8", "analysis_scale": "log", "effect_estimate": math.log(1.2), "standard_error": 0.1},
                {"study_id": "S2", "effect_measure": "risk_ratio", "estimand": "intention_to_treat", "outcome": "response", "time_point": "week_8", "analysis_scale": "log", "effect_estimate": math.log(1.5), "standard_error": 0.1},
            ],
        }
        result = self.pooler.pool(payload)
        self.assertEqual(result["back_transformed"]["transform"], "exp")
        self.assertAlmostEqual(result["back_transformed"]["fixed_effect"]["estimate"], math.sqrt(1.2 * 1.5), places=12)
        payload["analysis_scale"] = "identity"
        for row in payload["effects"]:
            row["analysis_scale"] = "identity"
        with self.assertRaisesRegex(ValueError, "ratio measures"):
            self.pooler.pool(payload)

    def test_pooling_requires_explicit_independence(self):
        payload = self.pool_payload()
        payload["independent_estimates"] = False
        with self.assertRaisesRegex(ValueError, "independent_estimates"):
            self.pooler.pool(payload)

    def test_pooling_requires_explicit_model_selection(self):
        payload = self.pool_payload()
        payload.pop("tau_squared_estimator")
        payload.pop("random_effects_inference")
        with self.assertRaisesRegex(ValueError, "tau_squared_estimator"):
            self.pooler.pool(payload)
        payload["tau_squared_estimator"] = "reml"
        with self.assertRaisesRegex(ValueError, "random_effects_inference"):
            self.pooler.pool(payload)

    def test_reml_hartung_knapp_reports_prediction_interval_and_warnings(self):
        result = self.pooler.pool(self.pool_payload((0.0, 3.0, 6.0), (0.2, 0.2, 0.2)))
        self.assertEqual(result["schema_version"], "2.0")
        self.assertEqual(result["model_selection"]["tau_squared_estimator"], "reml")
        self.assertGreater(result["random_effects"]["tau_squared"], 0)
        prediction = result["random_effects"]["prediction_interval"]
        self.assertEqual(prediction["status"], "estimated")
        self.assertLess(prediction["lower"], result["random_effects"]["estimate"])
        self.assertGreater(prediction["upper"], result["random_effects"]["estimate"])
        warning_codes = {item["code"] for item in result["warnings"]}
        self.assertIn("small_k", warning_codes)
        self.assertIn("high_heterogeneity", warning_codes)

    def test_hartung_knapp_two_study_prediction_interval_is_not_estimable(self):
        payload = self.pool_payload()
        payload["tau_squared_estimator"] = "dersimonian_laird"
        payload["random_effects_inference"] = "hartung_knapp"
        result = self.pooler.pool(payload)
        self.assertEqual(result["random_effects"]["prediction_interval"]["status"], "not_estimable")
        self.assertIn("dl_tau_squared", {item["code"] for item in result["warnings"]})

    def test_release_manifest_separates_local_and_publication_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data = base / "measurements.csv"
            data.write_bytes(b"sample,value\nS1,3.5\n")
            inventory_path = base / "inventory.json"
            payload = self.availability_payload({
                "artifact_id": "DATA-1", "artifact_class": "raw_data", "description": "Source measurements",
                "supports_claims": ["CLM-1"], "supports_results": [], "controller": "author team",
                "access_route": "within_article", "status": "ready", "version": "1", "format": "text/csv",
                "derived_from": [], "current_location": "measurements.csv",
            })
            inventory_path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.release.build(payload, inventory_path)
            self.assertTrue(result["local_package_ready"])
            self.assertFalse(result["publication_release_ready"])
            self.assertFalse(result["release_ready"])
            self.assertEqual(result["release_ready_deprecation"]["alias_of"], "publication_release_ready")
            self.assertEqual(result["file_verified_count"], 1)
            self.assertEqual(result["artifacts"][0]["sha256"], hashlib.sha256(data.read_bytes()).hexdigest())
            self.assertEqual(result["artifacts"][0]["checksum_verification"]["status"], "computed_no_prior_declaration")
            self.assertEqual(result["artifacts"][0]["supports_claims"], ["CLM-1"])
            self.assertIn("license_not_declared", {item["reason"] for item in result["publication_unresolved"]})

    def test_release_manifest_can_verify_public_identifier_and_license_assertions(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data = base / "data.csv"
            data.write_text("x\n1\n", encoding="utf-8")
            checksum = hashlib.sha256(data.read_bytes()).hexdigest()
            inventory_path = base / "inventory.json"
            payload = self.availability_payload({
                "artifact_id": "DATA-READY", "artifact_class": "raw_data", "description": "Deposited data",
                "supports_claims": ["CLM-1"], "supports_results": [], "controller": "author team",
                "access_route": "public_repository", "repository": "Example Repository",
                "identifier": "doi:10.1234/example", "license": "CC-BY-4.0", "status": "ready",
                "version": "1", "format": "text/csv", "derived_from": [], "current_location": "data.csv",
                "sha256": checksum,
                "identifier_verification": {"status": "verified", "checked_at": "2026-08-13", "source": "repository landing page", "value": "doi:10.1234/example"},
                "license_verification": {"status": "verified", "checked_at": "2026-08-13", "source": "repository metadata", "value": "CC-BY-4.0"},
            })
            inventory_path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.release.build(payload, inventory_path)
            self.assertTrue(result["local_package_ready"])
            self.assertTrue(result["publication_release_ready"])
            self.assertTrue(result["release_ready"])
            self.assertEqual(result["artifacts"][0]["checksum_verification"]["status"], "verified_match")
            self.assertEqual(result["publication_unresolved"], [])

    def test_release_manifest_lists_missing_file_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            inventory_path = base / "inventory.json"
            payload = self.availability_payload({
                "artifact_id": "DATA-2", "artifact_class": "raw_data", "description": "Expected measurements",
                "supports_claims": ["CLM-2"], "supports_results": [], "controller": "author team",
                "access_route": "within_article", "status": "ready", "version": "1", "format": "text/csv",
                "license": "CC-BY-4.0", "license_verification": {"status": "verified", "checked_at": "2026-08-13", "source": "article terms"},
                "derived_from": [], "current_location": "missing.csv",
            })
            inventory_path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.release.build(payload, inventory_path)
            self.assertFalse(result["local_package_ready"])
            self.assertFalse(result["publication_release_ready"])
            self.assertEqual(result["local_unresolved"], [{"artifact_id": "DATA-2", "reason": "local_file_not_found"}])

    def test_release_manifest_rejects_declared_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "data.csv").write_text("x\n1\n", encoding="utf-8")
            inventory_path = base / "inventory.json"
            payload = self.availability_payload({
                "artifact_id": "DATA-3", "artifact_class": "raw_data", "description": "Source data",
                "supports_claims": ["CLM-3"], "supports_results": [], "controller": "author team",
                "access_route": "within_article", "status": "ready", "version": "1", "format": "text/csv",
                "license": "CC-BY-4.0", "license_verification": {"status": "verified", "checked_at": "2026-08-13", "source": "article terms"},
                "derived_from": [], "current_location": "data.csv", "sha256": "0" * 64,
            })
            inventory_path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.release.build(payload, inventory_path)
            self.assertFalse(result["local_package_ready"])
            self.assertEqual(result["artifacts"][0]["checksum_verification"]["status"], "mismatch")
            self.assertEqual(result["local_unresolved"], [{"artifact_id": "DATA-3", "reason": "declared_checksum_mismatch"}])

    def test_release_manifest_rejects_unverified_or_mismatched_public_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "data.csv").write_text("x\n1\n", encoding="utf-8")
            inventory_path = base / "inventory.json"
            payload = self.availability_payload({
                "artifact_id": "DATA-4", "artifact_class": "raw_data", "description": "Deposited data",
                "supports_claims": ["CLM-4"], "supports_results": [], "controller": "author team",
                "access_route": "public_repository", "repository": "Example", "identifier": "doi:10.1/a",
                "license": "CC-BY-4.0", "status": "ready", "version": "1", "format": "text/csv",
                "derived_from": [], "current_location": "data.csv",
                "identifier_verification": {"status": "verified", "checked_at": "2026-08-13", "source": "landing page", "value": "doi:10.1/different"},
                "license_verification": {"status": "pending", "source": "repository metadata"},
            })
            inventory_path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.release.build(payload, inventory_path)
            self.assertTrue(result["local_package_ready"])
            self.assertFalse(result["publication_release_ready"])
            reasons = {item["reason"] for item in result["publication_unresolved"]}
            self.assertIn("identifier_verification_value_mismatch", reasons)
            self.assertIn("license_not_verified", reasons)

    def test_cost_aware_portfolio_covers_pairs_deterministically(self):
        payload = self.plan([
            self.experiment("E-all", ["H1", "H2", "H3"], 5),
            self.experiment("E-12", ["H1", "H2"], 1),
            self.experiment("E-13", ["H1", "H3"], 1),
            self.experiment("E-23", ["H2", "H3"], 1),
        ])
        result = self.portfolio.select(payload, budget=3)
        self.assertEqual([item["experiment_id"] for item in result["selected"]], ["E-12", "E-13", "E-23"])
        self.assertEqual(result["cost_unit"], "thousand_cny")
        self.assertEqual(result["coverage_fraction"], 1.0)
        self.assertEqual(result["uncovered_pairs"], [])

    def test_portfolio_requires_common_cost_unit(self):
        payload = self.plan([self.experiment("E-12", ["H1", "H2"], 1)])
        payload.pop("cost_unit")
        with self.assertRaisesRegex(ValueError, "cost_unit is required"):
            self.portfolio.select(payload, budget=2)
        payload["cost_unit"] = "thousand_cny"
        payload["experiments"][0]["cost_unit"] = "days"
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.portfolio.select(payload, budget=2)

    def test_portfolio_selects_required_dependency_closure_in_order(self):
        payload = self.plan([
            self.experiment("E-cal", ["H1", "H2"], 1),
            self.experiment("E-13", ["H1", "H3"], 1, depends_on=["E-cal"], required=True),
            self.experiment("E-23", ["H2", "H3"], 1),
        ])
        result = self.portfolio.select(payload, budget=3)
        self.assertEqual([item["experiment_id"] for item in result["selected"]], ["E-cal", "E-13", "E-23"])
        self.assertEqual(result["required_dependency_closure"], ["E-13", "E-cal"])
        self.assertEqual(result["selected"][0]["selection_reason"], "required_or_required_dependency")

    def test_portfolio_enforces_mutual_exclusion_and_reports_skip(self):
        payload = self.plan([
            self.experiment("E-12", ["H1", "H2"], 1, required=True, mutually_exclusive_with=["E-13"]),
            self.experiment("E-13", ["H1", "H3"], 1),
            self.experiment("E-23", ["H2", "H3"], 1),
        ])
        result = self.portfolio.select(payload, budget=3)
        selected_ids = {item["experiment_id"] for item in result["selected"]}
        self.assertIn("E-12", selected_ids)
        self.assertNotIn("E-13", selected_ids)
        skipped = {item["experiment_id"]: item["reasons"] for item in result["unselected_experiments"]}
        self.assertTrue(any(reason.startswith("mutual_exclusion_with_selected") for reason in skipped["E-13"]))

    def test_portfolio_excludes_ethically_pending_experiment(self):
        pending = self.experiment(
            "E-12", ["H1", "H2"], 0.1, eligible=False,
            feasibility_status="eligible", ethics_status="pending",
        )
        payload = self.plan([pending, self.experiment("E-13", ["H1", "H3"], 1)])
        result = self.portfolio.select(payload, budget=2)
        self.assertNotIn("E-12", {item["experiment_id"] for item in result["selected"]})
        self.assertEqual(result["ineligible_experiments"][0]["ethics_status"], "pending")

    def test_portfolio_rejects_ineligible_required_experiment(self):
        rejected = self.experiment(
            "E-12", ["H1", "H2"], 1, eligible=False,
            feasibility_status="eligible", ethics_status="rejected", required=True,
        )
        payload = self.plan([rejected, self.experiment("E-13", ["H1", "H3"], 1)])
        with self.assertRaisesRegex(ValueError, "required experiments or their dependencies are ineligible"):
            self.portfolio.select(payload, budget=3)

    def test_portfolio_rejects_required_bundle_over_budget_and_dependency_cycle(self):
        payload = self.plan([
            self.experiment("E-cal", ["H1", "H2"], 2),
            self.experiment("E-13", ["H1", "H3"], 2, depends_on=["E-cal"], required=True),
        ])
        with self.assertRaisesRegex(ValueError, "exceeding budget"):
            self.portfolio.select(payload, budget=3)
        payload = self.plan([
            self.experiment("E-12", ["H1", "H2"], 1, depends_on=["E-13"]),
            self.experiment("E-13", ["H1", "H3"], 1, depends_on=["E-12"]),
        ])
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            self.portfolio.select(payload, budget=3)


if __name__ == "__main__":
    unittest.main()
