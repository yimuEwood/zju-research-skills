from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


L1 = _load_module("generate_l1_census_v3", ROOT / "evals/generate_l1_census_v3.py")
SCORER = _load_module("score_portfolio_v3_for_l1", ROOT / "evals/score_portfolio_v3.py")


class L1CensusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.census = L1.build_census(ROOT)

    def test_census_covers_all_twenty_skills_and_six_checks(self):
        records = self.census["records"]
        by_skill: dict[str, set[str]] = {}
        for record in records:
            by_skill.setdefault(record["skill_id"], set()).add(record["case_id"])
            self.assertEqual(record["layer"], "L1_contract_conformance")
            self.assertEqual(record["stratum"], "release_census")
            self.assertEqual(record["capability_ids"], [])
            self.assertIs(record["critical_failure"], False)
            self.assertIsInstance(record["passed"], bool)
        self.assertEqual(len(by_skill), 20)
        self.assertEqual(len(records), 120)
        for check_ids in by_skill.values():
            self.assertEqual(check_ids, set(L1.CHECK_IDS))
        self.assertEqual(self.census["summary"]["complete_skills"], 20)
        self.assertEqual(self.census["summary"]["failed_records"], 0)

    def test_census_is_reproducible_and_manifested(self):
        again = L1.build_census(ROOT)
        self.assertEqual(self.census, again)
        manifest = self.census["input_manifest"]
        self.assertGreater(len(manifest), 20)
        self.assertEqual([row["path"] for row in manifest], sorted(row["path"] for row in manifest))
        for row in manifest:
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(self.census["input_tree_sha256"], r"^[0-9a-f]{64}$")

    def test_generator_records_a_real_failure_instead_of_defaulting_to_pass(self):
        real_exists = Path.is_file

        def hide_one_resource(path: Path) -> bool:
            normalized = str(path).replace("\\", "/")
            if normalized.endswith("skills/zju-literature-search/references/search-protocol.md"):
                return False
            return real_exists(path)

        with mock.patch.object(Path, "is_file", hide_one_resource):
            census = L1.build_census(ROOT)
        target = next(
            record for record in census["records"]
            if record["skill_id"] == "zju-literature-search"
            and record["case_id"] == "resource_resolution"
        )
        observation = next(
            row for row in census["observations"]
            if row["skill_id"] == "zju-literature-search"
            and row["check_id"] == "resource_resolution"
        )
        self.assertIs(target["passed"], False)
        self.assertTrue(any("missing local resource" in item for item in observation["failures"]))

    def test_l1_only_evidence_keeps_official_scores_null_and_beta(self):
        protocol = json.loads((ROOT / "evals/portfolio-protocol-v3.json").read_text(encoding="utf-8"))
        matrix = json.loads((ROOT / "evals/skill-evaluation-matrix-v3.json").read_text(encoding="utf-8"))
        results = L1.to_results_bundle(
            self.census,
            protocol,
            matrix,
            run_id="l1-contract-census-v3-test",
            skill_commit="0" * 40,
        )
        report = SCORER.score_portfolio(
            protocol,
            matrix,
            results,
            protocol_schema=json.loads((ROOT / "evals/portfolio-protocol-v3.schema.json").read_text(encoding="utf-8")),
            matrix_schema=json.loads((ROOT / "evals/skill-evaluation-matrix-v3.schema.json").read_text(encoding="utf-8")),
            results_schema=json.loads((ROOT / "evals/portfolio-results-v3.schema.json").read_text(encoding="utf-8")),
        )
        self.assertTrue(report["valid_input"], report["errors"])
        self.assertEqual(report["portfolio_status"], "beta")
        self.assertIs(report["official_portfolio_score"], None)
        self.assertIs(report["official_portfolio_score_ci95"], None)
        self.assertFalse(report["release_gate_passed"])
        self.assertEqual(report["evidence_coverage"]["L1_contract_conformance"]["complete_skills"], 20)
        for skill in report["skills"]:
            self.assertEqual(skill["layers"]["L1_contract_conformance"]["status"], "complete")
            self.assertEqual(skill["layers"]["L2_deterministic_function"]["status"], "not_run")
            self.assertEqual(skill["layers"]["L3_controlled_task_capability"]["status"], "not_run")
            self.assertEqual(skill["layers"]["L4_frozen_holdout_generalization"]["status"], "not_run")
            self.assertIs(skill["official_score"], None)

    def test_cli_writes_the_same_census(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "census.json"
            bundle_output = Path(directory) / "results.json"
            with mock.patch("sys.argv", [
                "generate_l1_census_v3.py",
                "--output", str(output),
                "--results-output", str(bundle_output),
                "--run-id", "l1-contract-census-cli",
                "--skill-commit", "1" * 40,
            ]):
                self.assertEqual(L1.main(), 0)
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written, self.census)
            bundle = json.loads(bundle_output.read_text(encoding="utf-8"))
            self.assertEqual(len(bundle["records"]), 120)
            self.assertTrue(all(record["run_id"] == "l1-contract-census-cli" for record in bundle["records"]))
            self.assertTrue(all(record["skill_commit"] == "1" * 40 for record in bundle["records"]))
            self.assertEqual(bundle["baseline_selection"], {})
            self.assertIsNone(bundle["rater_precommit"])
            self.assertIsNone(bundle["run_manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
