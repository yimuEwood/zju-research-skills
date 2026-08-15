from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals/l2_portfolio_execution.py"
CASES = ROOT / "evals/l2-cases-v3.json"
GAP_ORACLE_IDS = {
    ("zju-reference-audit", "claim_support"): "claim_atom_support_v1",
    ("zju-experiment-log", "append_only_correction"): "append_only_hash_chain_v1",
    ("zju-scientific-writing", "mode_fidelity"): "writing_mode_contract_v1",
    ("zju-evidence-synthesis", "protocol_selection"): "synthesis_protocol_decision_table_v1",
    ("zju-data-availability", "statement_consistency"): "data_statement_inventory_bijection_v1",
    ("zju-proposal-writer", "feasibility_risk"): "proposal_schedule_resource_risk_v1",
    ("zju-paper-to-patent", "claim_maps"): "patent_claim_dependency_support_v1",
    ("zju-paper-to-patent", "prior_art_separation"): "patent_prior_art_lane_separation_v1",
    ("zju-chemistry-databases", "condition_comparison"): "chemistry_condition_normalization_v1",
    ("zju-research-integrity", "neutral_triage"): "integrity_neutrality_contract_v1",
    ("zju-research-integrity", "scope_severity"): "integrity_scope_severity_matrix_v1",
    ("zju-research-integrity", "remediation_escalation"): "integrity_remediation_escalation_v1",
}


def load_module():
    spec = importlib.util.spec_from_file_location("l2_portfolio_execution_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class L2PortfolioExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.document = json.loads(CASES.read_text(encoding="utf-8"))

    def test_serialized_manifest_matches_current_capability_specific_builder(self):
        rebuilt = self.module.build_cases()
        self.assertEqual(self.document, rebuilt)
        self.assertEqual(self.module.validate_case_document(self.document), [])

    def test_every_skill_has_twenty_planned_cases_and_four_per_capability(self):
        matrix = json.loads((ROOT / "evals/skill-evaluation-matrix-v3.json").read_text(encoding="utf-8"))
        by_skill = defaultdict(list)
        hashes = set()
        for case in self.document["cases"]:
            by_skill[case["skill_id"]].append(case)
            self.assertEqual(case["fixture_sha256"], self.module.canonical_sha256(case["fixture"]))
            hashes.add(case["fixture_sha256"])
        self.assertEqual(len(self.document["cases"]), 400)
        self.assertEqual(self.document["blocked_case_count"], 0)
        self.assertEqual(len(hashes), 400)
        self.assertEqual(set(by_skill), {row["skill_id"] for row in matrix["skills"]})
        for skill in matrix["skills"]:
            rows = by_skill[skill["skill_id"]]
            self.assertEqual(len(rows), 20)
            counts = Counter(cap for row in rows for cap in row["capability_ids"])
            self.assertEqual(counts, Counter({cap["id"]: 4 for cap in skill["capabilities"]}))
            self.assertGreaterEqual(sum(row["stratum"] == "negative" for row in rows), 5)

    def test_each_capability_has_one_specific_behavior_and_oracle(self):
        signatures = defaultdict(set)
        for case in self.document["cases"]:
            key = (case["skill_id"], case["capability_ids"][0])
            signatures[key].add((case["measured_behavior"], case["oracle_id"]))
        self.assertEqual(len(signatures), 100)
        self.assertTrue(all(len(rows) == 1 for rows in signatures.values()))
        self.assertEqual(len({next(iter(rows))[0] for rows in signatures.values()}), 100)
        self.assertEqual(len({next(iter(rows))[1] for rows in signatures.values()}), 100)

    def test_new_gap_capabilities_use_frozen_executor_oracle_ids(self):
        counts = Counter()
        for case in self.document["cases"]:
            key = (case["skill_id"], case["capability_ids"][0])
            if key not in GAP_ORACLE_IDS:
                continue
            counts[key] += 1
            self.assertEqual(case["oracle_id"], GAP_ORACLE_IDS[key])
            self.assertTrue(any(
                assertion.get("path") == "test.details.oracle_id"
                and assertion.get("value") == GAP_ORACLE_IDS[key]
                for assertion in case["oracle"]["assertions"]
            ))
        self.assertEqual(counts, Counter({key: 4 for key in GAP_ORACLE_IDS}))

    def test_executable_cases_have_real_mutations_oracles_and_contrasts(self):
        for case in self.document["cases"]:
            if case["eligibility"] != "executable":
                self.assertIn((case["skill_id"], case["capability_ids"][0]), self.module.BLOCKED_CAPABILITIES)
                self.assertIsNone(case["oracle"])
                continue
            self.assertTrue(case["measured_behavior"])
            self.assertTrue(case["oracle_id"])
            self.assertTrue(case["input_mutations"])
            self.assertNotEqual(case["fixture"]["test_input"], case["fixture"]["control_input"])
            mutated_paths = {row["path"] for row in case["input_mutations"]}
            capability_fields = set(case["capability_input_fields"])
            self.assertTrue(
                any(
                    mutation == field
                    or mutation.startswith(field + ".")
                    or field.startswith(mutation + ".")
                    for mutation in mutated_paths
                    for field in capability_fields
                ),
                case["case_id"],
            )
            assertions = case["oracle"]["assertions"]
            self.assertTrue(any(row["path"] == "test.accepted" for row in assertions))
            self.assertTrue(any(row["path"] == "control.accepted" for row in assertions))
            self.assertNotIn("_l2_case_context", json.dumps(case, ensure_ascii=False))

    def test_every_executable_case_matches_oracle_twice(self):
        failures = []
        executable = 0
        for case in self.document["cases"]:
            if case["eligibility"] != "executable":
                continue
            executable += 1
            observed = self.module.execute_case(case)
            if not observed["passed"]:
                failures.append((case["case_id"], observed))
            else:
                self.assertTrue(observed["deterministic_repeat"])
        self.assertEqual(executable, 400)
        self.assertEqual(failures, [])

    def test_blocked_plans_never_become_passing_evidence(self):
        blocked = [case for case in self.document["cases"] if case["eligibility"] != "executable"]
        self.assertEqual(self.module.BLOCKED_CAPABILITIES, {})
        self.assertEqual(blocked, [])
        for case in blocked:
            observed = self.module.execute_case(case)
            self.assertFalse(observed["passed"])
            self.assertTrue(observed["blocked"])

    def test_fixture_tampering_fails_closed_before_executor(self):
        case = copy.deepcopy(next(row for row in self.document["cases"] if row["eligibility"] == "executable"))
        case["fixture"]["test_input"] = {"tampered": True}
        observed = self.module.execute_case(case)
        self.assertFalse(observed["passed"])
        self.assertEqual(observed["reason"], "fixture hash mismatch")


if __name__ == "__main__":
    unittest.main()
