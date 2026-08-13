from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "evals/fixtures/portfolio-capability-fixtures.json"


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class PortfolioCapabilityCoverageTests(unittest.TestCase):
    def test_fixture_registry_covers_every_skill_exactly_once(self):
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        declared = [row["skill"] for row in fixture["fixtures"]]
        installed = sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir())
        self.assertEqual(sorted(declared), installed)
        self.assertTrue(all(count == 1 for count in Counter(declared).values()))
        self.assertIn("not a blind scientific-quality score", fixture["scope"])
        for row in fixture["fixtures"]:
            self.assertTrue((ROOT / "skills" / row["skill"] / "scripts" / row["module"]).is_file(), row)
            self.assertTrue(row["fixture_kind"])
            self.assertTrue(row["expected"])

    def test_chemistry_fixture_separates_noncomparable_conditions(self):
        matrix = load_module("portfolio_chemistry_matrix", "skills/zju-chemistry-databases/scripts/build_evidence_matrix.py")
        result = matrix.build({"records": [
            {"record_id": "CHEM-1", "entity_id": "IK:ABC", "property_or_endpoint": "solubility", "value": 12.0, "unit": "mg/L", "basis": "free_base", "conditions": {"temperature_C": 25, "pH": 7}, "method_or_assay": "shake flask", "evidence_type": "experimental", "source_database": "primary", "source_anchor": "Table 1"},
            {"record_id": "CHEM-2", "entity_id": "IK:ABC", "property_or_endpoint": "solubility", "value": 19.0, "unit": "mg/L", "basis": "free_base", "conditions": {"temperature_C": 37, "pH": 7}, "method_or_assay": "shake flask", "evidence_type": "experimental", "source_database": "primary", "source_anchor": "Table 2"},
        ]})
        self.assertTrue(result["valid"], result)
        self.assertEqual(len(result["comparison_groups"]), 2)
        self.assertNotEqual(result["matrix_rows"][0]["comparison_group_id"], result["matrix_rows"][1]["comparison_group_id"])

    def test_data_availability_fixture_rejects_result_without_supporting_artifact(self):
        inventory = load_module("portfolio_data_inventory", "skills/zju-data-availability/scripts/validate_data_inventory.py")
        payload = {
            "workflow_version": "2.0", "computational_results_present": True,
            "artifacts": [
                {"artifact_id": "CODE", "artifact_class": "analysis_code", "description": "analysis", "supports_claims": ["C1"], "supports_results": ["RES-1"], "derived_from": [], "controller": "authors", "access_route": "public_repository", "status": "ready", "repository": "repo", "identifier": "doi:10.1/code", "version": "1", "format": "Python"},
                {"artifact_id": "ENV", "artifact_class": "environment", "description": "lock", "supports_claims": ["C1"], "supports_results": [], "derived_from": [], "controller": "authors", "access_route": "public_repository", "status": "ready", "repository": "repo", "identifier": "doi:10.1/env", "version": "1", "format": "lockfile"},
            ],
            "reproducibility_packages": [{"package_id": "RP-1", "result_ids": ["RES-2"], "artifact_ids": ["CODE", "ENV"], "environment_artifact_id": "ENV", "entrypoint": "python run.py", "expected_outputs": ["RES-2"], "verification": {"status": "not_run"}}],
        }
        result = inventory.validate(payload)
        self.assertFalse(result["valid"])
        self.assertTrue(any("no supporting inventory artifact" in finding["message"] for finding in result["findings"]))

    def test_reviewer_fixture_requires_resolution_evidence_and_actions(self):
        reviewer = load_module("portfolio_reviewer", "skills/zju-reviewer/scripts/validate_review_report.py")
        valid = {
            "workflow_version": "2.0", "mode": "single_review", "assessment_boundary": "Methods and Results",
            "concerns": [{"concern_id": "R1", "severity": "major", "blocking": True, "axis": "validity", "claim_pointer": "Results 2", "evidence_pointer": "Fig. 2", "claim_ids": ["C1"], "evidence_ids": ["E1"], "result_ids": ["RES-1"], "concern": "Control is missing", "why_it_matters": "The causal contrast is ambiguous", "requested_evidence": ["negative control"], "action_options": ["add the control", "bound the claim"], "resolution_test": "Control result resolves the alternative explanation"}],
        }
        self.assertTrue(reviewer.validate(valid)["response_handoff_ready"])
        invalid = json.loads(json.dumps(valid))
        invalid["concerns"][0]["requested_evidence"] = []
        invalid["concerns"][0]["action_options"] = []
        self.assertFalse(reviewer.validate(invalid)["valid"])

    def test_response_fixture_rejects_unreconciled_new_analysis(self):
        response = load_module("portfolio_response", "skills/zju-review-response/scripts/validate_response_tracker.py")
        item = {"comment_id": "R1.1", "concern_id": "R1", "action_id": "A1", "source_role": "reviewer", "verbatim_comment": "Run sensitivity analysis", "action_type": "new_analysis", "requested_action": "test robustness", "evidence_status": "verified", "evidence_ids": ["AN-1"], "result_ids": ["RES-2"], "status": "verified_complete", "response_text": "Added.", "manuscript_change": "Added result.", "manuscript_diff": {"before": "Absent", "after": "Present", "dependent_artifacts": ["Abstract"]}, "location": "Results 3"}
        self.assertFalse(response.validate({"workflow_version": "2.0", "items": [item]})["ready"])
        item["result_reconciliation"] = {"status": "passed", "registry_version": "2"}
        self.assertTrue(response.validate({"workflow_version": "2.0", "items": [item]})["ready"])

    def test_director_fixture_resolves_available_narrow_provider(self):
        resolver = load_module("portfolio_executor_resolver", "skills/zju-research-director/scripts/resolve_executor.py")
        registry = json.loads((ROOT / "skills/zju-research-director/references/executor-registry.yaml").read_text(encoding="utf-8"))
        provider = next(row for row in registry["providers"] if row["provider_id"] == "script.zju.statistics.execute_analysis")
        inventory = {"platform": "windows", "available": [provider["availability_probe"]["key"]]}
        result = resolver.resolve_executors(registry, inventory, [{"capability": "statistical_analysis", "accepts": ["tabular_data", "analysis_contract"], "produces": ["result_registry"]}])
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(result["selected"][0]["provider_id"], "script.zju.statistics.execute_analysis")
        self.assertFalse(result["execution_performed"])

    def test_director_shared_artifact_contract_rejects_wrong_mission(self):
        contract = load_module("portfolio_artifact_contract", "skills/zju-research-director/scripts/artifact_contract.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            artifact_file = base / "evidence.json"
            artifact_file.write_text("{}", encoding="utf-8")
            import hashlib
            artifact = {
                "artifact_id": "A-1", "artifact_type": "evidence_table", "path": "evidence.json",
                "sha256": hashlib.sha256(artifact_file.read_bytes()).hexdigest(), "schema_version": "1.0",
                "status": "created", "created_at": "2026-08-13T10:00:00+08:00",
                "provenance": {"mission_id": "MISSION-OTHER", "producer": "external", "producer_step_id": "S1"},
            }
            result = contract.validate_artifact(artifact, "MISSION-EXPECTED", base_dir=base)
            self.assertFalse(result["valid"])
            self.assertTrue(any(finding["code"] == "mission_mismatch" for finding in result["errors"]))

    def test_director_common_helpers_load_and_hash_documents(self):
        common = load_module("portfolio_director_common", "skills/zju-research-director/scripts/director_common.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "document.json"
            payload = {"b": 2, "a": 1}
            common.write_document(path, payload)
            self.assertEqual(common.load_document(path), payload)
            self.assertEqual(common.canonical_json(payload), '{"a":1,"b":2}')
            self.assertEqual(common.stable_hash(payload), common.stable_hash({"a": 1, "b": 2}))

    def test_director_validator_cli_core_rejects_non_object(self):
        validator = load_module("portfolio_validate_artifact", "skills/zju-research-director/scripts/validate_artifact.py")
        result = validator.validate("not-an-artifact", "MISSION-1")
        self.assertFalse(result["valid"])
        self.assertTrue(result["results"][0]["errors"])

    def test_capability_matrix_distinguishes_coverage_from_scientific_quality(self):
        audit = load_module("portfolio_capability_matrix", "evals/build_skill_capability_matrix.py")
        result = audit.build()
        self.assertEqual(result["skill_count"], 20)
        self.assertEqual(result["coverage_ready_count"], 20)
        self.assertIn("not scientific-quality scores", result["scope"])
        by_skill = {row["skill"]: row for row in result["skills"]}
        self.assertEqual(by_skill["zju-research-director"]["designed_eval_cases"], 20)
        self.assertTrue(all(row["designed_eval_cases"] >= 10 for row in result["skills"]))
        self.assertTrue(all(row["scientific_quality_status"] == "requires_blind_execution_and_independent_rating" for row in result["skills"]))

    def test_legacy_public_fixtures_have_explicit_consumption_status(self):
        stats = json.loads((ROOT / "evals/fixtures/statistics-defects-20.json").read_text(encoding="utf-8"))
        paper = json.loads((ROOT / "evals/fixtures/paper-reader-public-corpus.json").read_text(encoding="utf-8"))
        experiment = json.loads((ROOT / "evals/fixtures/experiment-log-inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(stats["consumption_status"], "not_consumed_by_release_score")
        self.assertEqual(paper["consumption_status"], "network_dependent_forward_test_not_run_in_unit_suite")
        self.assertEqual(experiment["consumption_status"], "design_fixture_requires_evaluator_supplied_binary_inputs")


if __name__ == "__main__":
    unittest.main()
