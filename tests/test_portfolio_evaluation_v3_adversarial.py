"""Adversarial checks for protocol-v3's fail-closed evidence boundary.

These tests deliberately use a small number of task cases.  They are not
capability benchmarks; they verify that plausible-looking, high-scoring JSON
cannot be promoted when its provenance or canonical scoring bindings fail.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "evals" / "score_portfolio_v3.py"
SPEC = importlib.util.spec_from_file_location("score_portfolio_v3_adversarial", MODULE_PATH)
SCORER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SCORER)


class PortfolioProtocolV3AdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = cls._read_json("evals/portfolio-protocol-v3.json")
        cls.matrix = cls._read_json("evals/skill-evaluation-matrix-v3.json")
        cls.rows = {row["skill_id"]: row for row in cls.matrix["skills"]}

    @staticmethod
    def _read_json(relative_path: str) -> dict:
        return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))

    @staticmethod
    def _sha(label: str) -> str:
        import hashlib

        return hashlib.sha256(label.encode("utf-8")).hexdigest()

    def _baseline_selection(self) -> dict[str, dict]:
        selections: dict[str, dict] = {}
        for skill_id, row in self.rows.items():
            selection = {
                "baseline_id": row["strongest_open_source_baseline_candidates"][0],
                "source_commit": "b" * 40,
                "selected_at": "2026-08-12T00:00:00Z",
                "selection_rationale": "Preselected strongest compatible declared candidate before execution.",
                "selected_before_first_run": True,
                "baseline_packet_sha256": self._sha(f"baseline-packet:{skill_id}"),
            }
            selection["selection_lock_sha256"] = SCORER._canonical_sha256(selection)
            selections[skill_id] = selection
        return selections

    def _base_results(self, *, holdout: bool = False) -> dict:
        rater_precommit = {
            "primary_rater_ids": ["rater-a", "rater-b"],
            "adjudicator_id": "rater-c",
            "rubric_sha256": self._sha("fixed-v3-rubric"),
        }
        rater_precommit["precommit_sha256"] = SCORER._canonical_sha256(rater_precommit)
        artifact_hash = self._sha("self-declared-artifact") if holdout else None
        return {
            "schema_version": "3.0",
            "protocol_id": self.protocol["protocol_id"],
            "run_id": "adversarial-run",
            "skill_commit": "a" * 40,
            "protocol_sha256": SCORER._canonical_sha256(self.protocol),
            "capability_matrix_sha256": SCORER._canonical_sha256(self.matrix),
            "run_manifest_sha256": self._sha("self-declared-run-manifest"),
            "baseline_selection": self._baseline_selection(),
            "rater_precommit": rater_precommit,
            "holdout_declaration": {
                "frozen": holdout,
                "unseen": holdout,
                "first_attempt": holdout,
                "independent_administration": holdout,
                "lock_sha256": artifact_hash,
                "case_document_sha256": artifact_hash,
                "case_fingerprints_sha256": artifact_hash,
                "first_attempt_registry_sha256": artifact_hash,
                "allocation_commitment_sha256": artifact_hash,
                "allocation_reveal_sha256": artifact_hash,
                "independent_administration_attestation_sha256": artifact_hash,
            },
            "protocol_deviations": [],
            "records": [],
        }

    def _rating(self, *, rater_id: str, call_id: str, response_sha256: str) -> dict:
        return {
            "rater_id": rater_id,
            "call_id": call_id,
            "model": "fixed-test-rater",
            "rated_at": "2026-08-12T03:00:00Z",
            "response_sha256": response_sha256,
            "rubric_sha256": self._sha("fixed-v3-rubric"),
            "dimension_scores": {
                "task_completeness": 4,
                "evidence_traceability": 4,
                "scientific_validity": 4,
                "integrity": 4,
                "usability": 4,
            },
            "score": 100,
            "critical_failure": False,
            "gold_checks": [{"check_id": "required-output", "met": True}],
        }

    def _task_case(self, skill_id: str, case_id: str, *, layer: str = SCORER.LAYER_IDS[2]) -> list[dict]:
        capability_id = self.rows[skill_id]["capabilities"][0]["id"]
        fingerprint = self._sha(f"case:{layer}:{skill_id}:{case_id}")
        gold_ids = ["required-output"]
        records: list[dict] = []
        for arm, blind_label in zip(SCORER.TASK_ARMS, ("A", "B", "C"), strict=True):
            response_sha = self._sha(f"response:{layer}:{skill_id}:{case_id}:{arm}")
            record = {
                "skill_id": skill_id,
                "layer": layer,
                "case_id": case_id,
                "case_fingerprint": fingerprint,
                "stratum": "nominal",
                "capability_ids": [capability_id],
                "critical_failure": False,
                "run_id": "adversarial-run",
                "skill_commit": "a" * 40,
                "protocol_sha256": SCORER._canonical_sha256(self.protocol),
                "capability_matrix_sha256": SCORER._canonical_sha256(self.matrix),
                "evidence_sha256": self._sha(f"evidence:{layer}:{skill_id}:{case_id}:{arm}"),
                "instruction_packet_sha256": self._sha(f"instructions:{layer}:{skill_id}:{case_id}"),
                "fixture_sha256": self._sha(f"fixture:{layer}:{skill_id}:{case_id}"),
                "gold_check_ids": gold_ids,
                "gold_criteria_sha256": SCORER._canonical_sha256(gold_ids),
                "arm": arm,
                "blind_label": blind_label,
                "baseline_id": (
                    self.rows[skill_id]["strongest_open_source_baseline_candidates"][0]
                    if arm == "strongest_open_source_baseline"
                    else None
                ),
                "score": 100,
                "gold_met": 1,
                "gold_total": 1,
                "response_sha256": response_sha,
                "primary_ratings": [
                    self._rating(
                        rater_id="rater-a",
                        call_id=f"call:{layer}:{skill_id}:{case_id}:{arm}:a",
                        response_sha256=response_sha,
                    ),
                    self._rating(
                        rater_id="rater-b",
                        call_id=f"call:{layer}:{skill_id}:{case_id}:{arm}:b",
                        response_sha256=response_sha,
                    ),
                ],
                "adjudication": None,
            }
            records.append(record)
        return records

    @staticmethod
    def _untrusted_verification_shell() -> dict:
        """A parseable attacker-authored shell, never valid verification."""
        return {
            "schema_version": "3.0",
            "verification_id": "attacker-verification",
            "verified_at": "2026-08-12T05:00:00Z",
            "verifier_id": "attacker",
            "verification_key_id": "unregistered-attacker-key",
            "verification_signature_ed25519": "00" * 64,
            "run_manifest": {},
            "holdout_lock": {},
            "first_attempt_registry": {},
            "allocation_reveal": {},
            "administration_attestation": {},
        }

    def _score(self, results: dict, verification: dict | None = None) -> dict:
        return SCORER.score_portfolio(
            self.protocol,
            self.matrix,
            results,
            verification=verification,
        )

    def test_all_twenty_high_score_self_reports_without_verification_cannot_be_stable(self):
        results = self._base_results()
        for index, skill_id in enumerate(self.rows):
            results["records"].extend(self._task_case(skill_id, f"SELF-{index:02d}"))

        report = self._score(results)

        self.assertEqual({record["skill_id"] for record in results["records"]}, set(self.rows))
        self.assertTrue(all(record["score"] == 100 for record in results["records"]))
        self.assertFalse(report["valid_input"])
        self.assertEqual(report["portfolio_status"], "invalid")
        self.assertFalse(report["release_gate_passed"])
        self.assertIsNone(report["official_portfolio_score"])
        self.assertTrue(
            any("requires a verified protocol-artifact bundle" in error for error in report["errors"]),
            report["errors"],
        )

    def test_record_score_cannot_override_canonical_rater_aggregate(self):
        skill_id = next(iter(self.rows))
        results = self._base_results()
        results["records"] = self._task_case(skill_id, "FORGED-SCORE")
        results["records"][2]["score"] = 99

        report = self._score(results)

        self.assertTrue(
            any("record score is not the canonical rater aggregate" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertFalse(report["release_gate_passed"])

    def test_response_hash_cannot_be_reused_across_arms(self):
        skill_id = next(iter(self.rows))
        results = self._base_results()
        results["records"] = self._task_case(skill_id, "REUSED-RESPONSE")
        reused_hash = results["records"][0]["response_sha256"]
        results["records"][1]["response_sha256"] = reused_hash
        for rating in results["records"][1]["primary_ratings"]:
            rating["response_sha256"] = reused_hash

        report = self._score(results)

        self.assertTrue(
            any("response_sha256 is reused across arms or cases" in error for error in report["errors"]),
            report["errors"],
        )

    def test_baseline_must_be_one_of_the_predeclared_candidates(self):
        skill_id = next(iter(self.rows))
        results = self._base_results()
        selection = results["baseline_selection"][skill_id]
        selection["baseline_id"] = "invented-after-seeing-results"
        selection["selection_lock_sha256"] = SCORER._canonical_sha256(
            {key: value for key, value in selection.items() if key != "selection_lock_sha256"}
        )
        results["records"] = self._task_case(skill_id, "CHERRY-PICKED-BASELINE")
        baseline_record = next(
            record for record in results["records"]
            if record["arm"] == "strongest_open_source_baseline"
        )
        baseline_record["baseline_id"] = selection["baseline_id"]

        report = self._score(results, self._untrusted_verification_shell())

        self.assertTrue(
            any("selected baseline is not a declared candidate" in error for error in report["errors"]),
            report["errors"],
        )

    def test_record_commit_must_match_the_result_bundle_commit(self):
        skill_id = next(iter(self.rows))
        results = self._base_results()
        results["records"] = self._task_case(skill_id, "COMMIT-SWAP")
        results["records"][0]["skill_commit"] = "c" * 40

        report = self._score(results)

        self.assertTrue(
            any("skill_commit is not bound to the result bundle" in error for error in report["errors"]),
            report["errors"],
        )

    def test_holdout_declaration_hash_must_bind_the_supplied_lock_artifact(self):
        skill_id = next(iter(self.rows))
        results = self._base_results(holdout=True)
        results["records"] = self._task_case(
            skill_id,
            "HOLDOUT-HASH-SWAP",
            layer=SCORER.LAYER_IDS[3],
        )

        report = self._score(results, self._untrusted_verification_shell())

        self.assertTrue(
            any(
                "holdout declaration lock_sha256 does not bind the supplied artifact" in error
                for error in report["errors"]
            ),
            report["errors"],
        )

    def test_attacker_verification_key_is_not_trusted(self):
        skill_id = next(iter(self.rows))
        results = self._base_results()
        results["records"] = self._task_case(skill_id, "UNREGISTERED-KEY")

        report = self._score(results, copy.deepcopy(self._untrusted_verification_shell()))

        self.assertTrue(
            any("verification key was not independently pre-registered" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertFalse(report["release_gate_passed"])

    def test_scorer_rejects_post_signature_result_rating_and_artifact_replacement(self):
        skill_id = next(iter(self.rows))
        results = self._base_results()
        results["records"] = self._task_case(skill_id, "SIGNED-BINDING")
        verification = {
            "schema_version": "3.0", "verification_id": "binding-test",
            "verified_at": "2026-08-12T05:00:00Z", "verifier_id": "external-admin",
            "verification_key_id": "unit-external-key", "run_manifest": {},
            "holdout_lock": {}, "first_attempt_registry": {}, "allocation_reveals": [],
            "response_manifests": [], "artifact_manifests": [], "ratings_locks": [],
            "execution_event_manifests": [], "administration_attestation": {},
            "portfolio_results_sha256": SCORER._canonical_sha256(results),
            "portfolio_results_file_sha256": "a" * 64,
            "portfolio_results_size_bytes": 100,
            "portfolio_results_serialization": "source-file-bytes",
        }
        verification["evidence_components_sha256"] = SCORER._evidence_component_hash(
            run_manifest={}, response_manifests=[], artifact_manifests=[], ratings_locks=[],
            execution_event_manifests=[], allocation_reveals=[],
        )
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        payload = json.dumps(
            verification, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        verification["verification_signature_ed25519"] = private.sign(payload).hex()

        original_lookup = SCORER._trusted_verifier_public_key
        SCORER._trusted_verifier_public_key = lambda key_id, errors: public
        try:
            forged_results = copy.deepcopy(results)
            forged_results["records"][0]["score"] = 99
            result_errors: list[str] = []
            SCORER._validate_task_bundle(
                forged_results, set(self.rows), self.matrix, copy.deepcopy(verification), result_errors,
                results_file_sha256="a" * 64, results_size_bytes=100,
            )
            self.assertTrue(any("exact portfolio results" in error for error in result_errors), result_errors)
            byte_errors: list[str] = []
            SCORER._validate_task_bundle(
                results, set(self.rows), self.matrix, copy.deepcopy(verification), byte_errors,
                results_file_sha256="b" * 64, results_size_bytes=100,
            )
            self.assertTrue(any("results file bytes" in error for error in byte_errors), byte_errors)

            for section, field in (
                ("ratings_locks", "ratings_lock_sha256"),
                ("artifact_manifests", "artifact_manifest_sha256"),
            ):
                forged_bundle = copy.deepcopy(verification)
                forged_bundle[section] = [{"layer": SCORER.LAYER_IDS[2], field: "f" * 64}]
                bundle_errors: list[str] = []
                SCORER._validate_task_bundle(
                    results, set(self.rows), self.matrix, forged_bundle, bundle_errors
                )
                self.assertTrue(
                    any("Ed25519 signature is invalid" in error for error in bundle_errors),
                    bundle_errors,
                )
        finally:
            SCORER._trusted_verifier_public_key = original_lookup


if __name__ == "__main__":
    unittest.main()
