import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "evals" / "score_portfolio_v3.py"
SPEC = importlib.util.spec_from_file_location("score_portfolio_v3", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PortfolioProtocolV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((ROOT / "evals" / "portfolio-protocol-v3.json").read_text(encoding="utf-8"))
        cls.matrix = json.loads((ROOT / "evals" / "skill-evaluation-matrix-v3.json").read_text(encoding="utf-8"))

    def test_matrix_covers_exactly_repository_twenty_skills(self):
        matrix_skills = {row["skill_id"] for row in self.matrix["skills"]}
        repository_skills = {
            path.name for path in (ROOT / "skills").iterdir()
            if path.is_dir() and path.name.startswith("zju-") and (path / "SKILL.md").exists()
        }
        self.assertEqual(matrix_skills, repository_skills)
        self.assertEqual(len(matrix_skills), 20)
        for row in self.matrix["skills"]:
            self.assertGreaterEqual(len(row["capabilities"]), 5)
            self.assertEqual(len({item["id"] for item in row["capabilities"]}), len(row["capabilities"]))
            self.assertTrue(row["strongest_open_source_baseline_candidates"])

    def test_weights_sum_to_one_and_minimum_workload_is_explicit(self):
        layers = self.protocol["layers"]
        self.assertAlmostEqual(sum(layer["weight"] for layer in layers.values()), 1.0)
        self.assertEqual(layers["L1_contract_conformance"]["minimum_n_per_skill"], 6)
        self.assertEqual(layers["L2_deterministic_function"]["minimum_n_per_skill"], 20)
        self.assertEqual(layers["L3_controlled_task_capability"]["minimum_n_per_skill"], 12)
        self.assertEqual(layers["L4_frozen_holdout_generalization"]["minimum_n_per_skill"], 15)

    def test_empty_current_evidence_withholds_all_official_scores(self):
        results = json.loads((ROOT / "evals" / "current-portfolio-evidence-v3.json").read_text(encoding="utf-8"))
        report = MODULE.score_portfolio(self.protocol, self.matrix, results)
        self.assertTrue(report["valid_input"])
        self.assertEqual(report["portfolio_status"], "beta")
        self.assertIsNone(report["official_portfolio_score"])
        self.assertFalse(report["release_gate_passed"])
        self.assertEqual(len(report["skills"]), 20)
        self.assertTrue(all(skill["official_score"] is None for skill in report["skills"]))
        self.assertTrue(all(skill["status"] == "not_evaluated" for skill in report["skills"]))
        self.assertEqual(report["evidence_coverage"]["L4_frozen_holdout_generalization"]["complete_skills"], 0)
        self.assertIsNone(results["skill_commit"])

    def test_all_three_json_schemas_validate_current_documents(self):
        protocol_schema = json.loads((ROOT / "evals" / "portfolio-protocol-v3.schema.json").read_text(encoding="utf-8"))
        matrix_schema = json.loads((ROOT / "evals" / "skill-evaluation-matrix-v3.schema.json").read_text(encoding="utf-8"))
        results_schema = json.loads((ROOT / "evals" / "portfolio-results-v3.schema.json").read_text(encoding="utf-8"))
        results = json.loads((ROOT / "evals" / "current-portfolio-evidence-v3.json").read_text(encoding="utf-8"))
        report = MODULE.score_portfolio(
            self.protocol,
            self.matrix,
            results,
            protocol_schema=protocol_schema,
            matrix_schema=matrix_schema,
            results_schema=results_schema,
        )
        self.assertTrue(report["valid_input"], report["errors"])

    def test_schema_rejects_malformed_results_before_scoring(self):
        schema = json.loads((ROOT / "evals" / "portfolio-results-v3.schema.json").read_text(encoding="utf-8"))
        results = self._base_results()
        results["holdout_declaration"].pop("first_attempt")
        report = MODULE.score_portfolio(self.protocol, self.matrix, results, results_schema=schema)
        self.assertFalse(report["valid_input"])
        self.assertTrue(any("schema validation failed" in error for error in report["errors"]))

    def test_schema_requires_real_commit_once_records_exist(self):
        schema = json.loads((ROOT / "evals" / "portfolio-results-v3.schema.json").read_text(encoding="utf-8"))
        results = self._base_results()
        results["skill_commit"] = None
        skill = self.matrix["skills"][0]["skill_id"]
        results["records"].append(self._record(
            skill,
            "L2_deterministic_function",
            "DET-01",
            capability_ids=[self.matrix["skills"][0]["capabilities"][0]["id"]],
            passed=True,
        ))
        report = MODULE.score_portfolio(self.protocol, self.matrix, results, results_schema=schema)
        self.assertFalse(report["valid_input"])
        self.assertTrue(any("skill_commit" in error for error in report["errors"]))

    def test_structural_layer_alone_is_not_a_capability_score(self):
        results = self._base_results()
        skill = self.matrix["skills"][0]["skill_id"]
        for check_id in self.protocol["layers"]["L1_contract_conformance"]["required_check_ids"]:
            results["records"].append(self._record(skill, "L1_contract_conformance", check_id, passed=True))
        report = MODULE.score_portfolio(self.protocol, self.matrix, results)
        skill_report = next(row for row in report["skills"] if row["skill_id"] == skill)
        self.assertEqual(skill_report["layers"]["L1_contract_conformance"]["score"], 100.0)
        self.assertIsNone(skill_report["official_score"])
        self.assertEqual(skill_report["status"], "incomplete_beta")
        self.assertIn("Engineering readiness only", skill_report["layers"]["L1_contract_conformance"]["interpretation"])

    def test_frozen_holdout_declaration_is_required_for_l4_completion(self):
        results = self._base_results()
        skill_row = self.matrix["skills"][0]
        skill = skill_row["skill_id"]
        capabilities = [item["id"] for item in skill_row["capabilities"]]
        strata = self.protocol["layers"]["L4_frozen_holdout_generalization"]["required_strata"]
        for index in range(15):
            case_id = f"H-{index:02d}"
            for arm, score in zip(MODULE.TASK_ARMS, (70, 75, 90)):
                results["records"].append(self._record(
                    skill,
                    "L4_frozen_holdout_generalization",
                    case_id,
                    stratum=strata[index % len(strata)],
                    capability_ids=capabilities,
                    arm=arm,
                    score=score,
                ))
        report = MODULE.score_portfolio(self.protocol, self.matrix, results)
        skill_report = next(row for row in report["skills"] if row["skill_id"] == skill)
        layer = skill_report["layers"]["L4_frozen_holdout_generalization"]
        self.assertEqual(layer["n_cases"], 15)
        self.assertFalse(layer["holdout_ready"])
        self.assertEqual(layer["status"], "incomplete")
        self.assertIsNone(skill_report["official_score"])

    def test_incomplete_task_arm_set_is_invalid(self):
        results = self._base_results()
        skill = self.matrix["skills"][0]["skill_id"]
        capabilities = [item["id"] for item in self.matrix["skills"][0]["capabilities"]]
        results["records"].append(self._record(
            skill,
            "L3_controlled_task_capability",
            "DEV-01",
            stratum="nominal",
            capability_ids=capabilities,
            arm="distilled_skill",
            score=90,
        ))
        report = MODULE.score_portfolio(self.protocol, self.matrix, results)
        self.assertFalse(report["valid_input"])
        self.assertTrue(any("incomplete arm set" in error for error in report["errors"]))

    def test_unknown_capability_id_is_invalid(self):
        results = self._base_results()
        skill = self.matrix["skills"][0]["skill_id"]
        results["records"].append(self._record(
            skill,
            "L2_deterministic_function",
            "DET-01",
            stratum="nominal",
            capability_ids=["invented_capability"],
            passed=True,
        ))
        report = MODULE.score_portfolio(self.protocol, self.matrix, results)
        self.assertFalse(report["valid_input"])
        self.assertTrue(any("unknown capability IDs" in error for error in report["errors"]))

    def test_cli_writes_beta_report_and_uses_nonzero_gate_exit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "score.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--results",
                    str(ROOT / "evals" / "current-portfolio-evidence-v3.json"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            stored = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(stored["portfolio_status"], "beta")
            self.assertFalse(stored["release_gate_passed"])
            self.assertIsNone(stored["official_portfolio_score"])

    def test_self_declared_high_quality_skill_cannot_become_stable(self):
        protocol = json.loads(json.dumps(self.protocol))
        protocol["bootstrap"]["iterations"] = 200
        results = self._base_results()
        results["holdout_declaration"] = {
            "frozen": True,
            "unseen": True,
            "first_attempt": True,
            "independent_administration": True,
            "lock_sha256": "c" * 64,
        }
        skill_row = self.matrix["skills"][0]
        skill = skill_row["skill_id"]
        capabilities = [item["id"] for item in skill_row["capabilities"]]
        results["baseline_selection"][skill] = {
            "baseline_id": "pinned-baseline",
            "source_commit": "d" * 40,
            "selection_rationale": "Selected before case execution from the strongest compatible candidates.",
        }
        for check_id in protocol["layers"]["L1_contract_conformance"]["required_check_ids"]:
            results["records"].append(self._record(skill, "L1_contract_conformance", check_id, passed=True))
        for index in range(20):
            results["records"].append(self._record(
                skill,
                "L2_deterministic_function",
                f"DET-{index:02d}",
                stratum="boundary" if index < 5 else "nominal",
                capability_ids=[capabilities[index % len(capabilities)]],
                passed=True,
            ))
        l3_strata = protocol["layers"]["L3_controlled_task_capability"]["required_strata"]
        for index in range(12):
            for arm, score in zip(MODULE.TASK_ARMS, (60, 70, 90)):
                results["records"].append(self._record(
                    skill,
                    "L3_controlled_task_capability",
                    f"DEV-{index:02d}",
                    stratum=l3_strata[index % len(l3_strata)],
                    capability_ids=capabilities,
                    arm=arm,
                    score=score,
                ))
        l4_strata = protocol["layers"]["L4_frozen_holdout_generalization"]["required_strata"]
        for index in range(15):
            for arm, score in zip(MODULE.TASK_ARMS, (60, 70, 90)):
                results["records"].append(self._record(
                    skill,
                    "L4_frozen_holdout_generalization",
                    f"HOLD-{index:02d}",
                    stratum=l4_strata[index % len(l4_strata)],
                    capability_ids=capabilities,
                    arm=arm,
                    score=score,
                ))
        report = MODULE.score_portfolio(protocol, self.matrix, results)
        skill_report = next(row for row in report["skills"] if row["skill_id"] == skill)
        self.assertEqual(skill_report["status"], "invalid")
        self.assertIsNone(skill_report["official_score"])
        self.assertFalse(report["valid_input"])
        self.assertTrue(any(
            "verified protocol-artifact bundle" in error or "repository-pinned protocol" in error
            for error in report["errors"]
        ))
        self.assertIsNone(report["official_portfolio_score"])
        self.assertFalse(report["release_gate_passed"])

    @staticmethod
    def _base_results():
        return {
            "schema_version": "3.0",
            "protocol_id": "zju-portfolio-twenty-skill-v3",
            "run_id": "unit-test",
            "skill_commit": "a" * 40,
            "baseline_selection": {},
            "holdout_declaration": {
                "frozen": False,
                "unseen": False,
                "first_attempt": False,
                "independent_administration": False,
                "lock_sha256": None,
            },
            "protocol_deviations": [],
            "records": [],
        }

    @staticmethod
    def _record(skill, layer, case_id, *, stratum="contract", capability_ids=None, arm=None, passed=None, score=90):
        record = {
            "skill_id": skill,
            "layer": layer,
            "case_id": case_id,
            "stratum": stratum,
            "capability_ids": capability_ids or [],
            "critical_failure": False,
        }
        if layer in {"L1_contract_conformance", "L2_deterministic_function"}:
            record["passed"] = bool(passed)
        else:
            record.update({
                "arm": arm,
                "score": score,
                "gold_met": 4,
                "gold_total": 4,
                "response_sha256": "b" * 64,
                "adjudication_resolved": True,
                "primary_ratings": [
                    {"rater_id": "rater-a", "score": score, "critical_failure": False},
                    {"rater_id": "rater-b", "score": score, "critical_failure": False},
                ],
            })
        return record


if __name__ == "__main__":
    unittest.main()
