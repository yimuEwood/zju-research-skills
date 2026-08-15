from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals/blind_eval_operations_v3.py"


def load_module():
    spec = importlib.util.spec_from_file_location("blind_eval_operations_v3_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class BlindEvalOperationsV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.matrix = {
            "skills": [{
                "skill_id": "zju-literature-search",
                "strongest_open_source_baseline_candidates": ["nature-academic-search"],
                "capabilities": [{"id": f"cap_{index}"} for index in range(1, 6)],
            }]
        }
        cls.protocol = {
            "layers": {
                "L4_frozen_holdout_generalization": {
                    "minimum_n_per_skill": 15, "minimum_per_stratum": 3,
                    "minimum_cases_per_capability": 2,
                    "required_strata": ["nominal", "incomplete_input", "adversarial", "cross_domain", "artifact_or_handoff"],
                },
                "L3_controlled_task_capability": {
                    "minimum_n_per_skill": 12, "minimum_per_stratum": 3,
                    "minimum_cases_per_capability": 2,
                    "required_strata": ["nominal", "incomplete_input", "edge_case", "cross_skill_handoff"],
                },
            }
        }

    def task_document(self, count=6):
        strata = ["nominal", "incomplete_input", "adversarial", "cross_domain", "artifact_or_handoff"]
        return {
            "schema_version": "3.0", "layer": "L4_frozen_holdout_generalization",
            "cases": [{
                "case_id": f"HLD-{index:02d}", "skill_id": "zju-literature-search",
                "stratum": strata[index % len(strata)],
                "prompt": f"Independently solve frozen task number {index}.",
                "fixture": {"input": index}, "capability_ids": [f"cap_{index % 5 + 1}"],
                "gold_checks": [{"check_id": "G1", "criterion": f"Observable required output for task {index}."}],
            } for index in range(count)],
        }

    def rating_records(self, document):
        rows = []
        for case in document["cases"]:
            for label in self.module.LABELS:
                response_sha = self.module.canonical_sha256({"case": case["case_id"], "label": label})
                ratings = [
                    {
                        "rater_id": rater, "call_id": f"{rater}-{case['case_id']}-{label}",
                        "rated_at": "2026-08-15T11:55:00+08:00",
                        "response_sha256": response_sha, "score": 80,
                    }
                    for rater in ("r1", "r2")
                ]
                rows.append({
                    "layer": document["layer"], "skill_id": case["skill_id"],
                    "case_id": case["case_id"], "blind_label": label,
                    "response_sha256": response_sha, "primary_ratings": ratings,
                    "adjudication": None,
                })
        return rows

    def ratings_lock(self, document, *, sealed_at="2026-08-15T11:59:00+08:00"):
        return self.module.build_ratings_lock(
            self.rating_records(document), run_id="independent-run-001",
            layer=document["layer"], sealed_at=sealed_at,
            response_manifest_sha256="a" * 64,
        )

    def test_rename_only_duplicate_and_known_case_contamination_fail_closed(self):
        document = self.task_document(2)
        document["cases"][1] = copy.deepcopy(document["cases"][0])
        document["cases"][1]["case_id"] = "RENAMED-ONLY"
        with self.assertRaisesRegex(self.module.ProtocolError, "rename-only"):
            self.module.validate_task_document(document, matrix=self.matrix, protocol=self.protocol, require_full_portfolio=False)
        clean = self.task_document(1)
        known = [self.module.case_fingerprint(clean["cases"][0])]
        with self.assertRaisesRegex(self.module.ProtocolError, "overlaps known"):
            self.module.validate_task_document(clean, matrix=self.matrix, protocol=self.protocol, require_full_portfolio=False, known_fingerprints=known)

    def test_committed_latin_square_is_balanced_and_secret_sensitive(self):
        document = self.task_document(6)
        secret = "independent-secret-0123456789abcdef"
        first = self.module.latin_square_assignments(document, secret=secret, nonce="nonce-0123456789", freeze_id="freeze-001")
        second = self.module.latin_square_assignments(document, secret=secret, nonce="nonce-0123456789", freeze_id="freeze-001")
        changed = self.module.latin_square_assignments(document, secret=secret + "x", nonce="nonce-0123456789", freeze_id="freeze-001")
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertEqual(len(first), 18)
        for label in self.module.LABELS:
            counts = {arm: sum(row["blind_label"] == label and row["arm"] == arm for row in first) for arm in self.module.ARMS}
            self.assertEqual(set(counts.values()), {2})

    def test_holdout_freeze_first_attempt_and_reveal_order(self):
        document = self.task_document(6)
        secret, nonce, freeze_id = "independent-secret-0123456789abcdef", "nonce-0123456789", "freeze-001"
        commitment = self.module.allocation_commitment(secret, nonce, freeze_id, document["layer"])
        lock = self.module.build_holdout_lock(
            document, freeze_id=freeze_id, frozen_at="2026-08-15T09:00:00+08:00",
            commitment_sha256=commitment, matrix=self.matrix, protocol=self.protocol,
            require_full_portfolio=False,
        )
        registry = self.module.build_first_attempt_registry(
            lock, run_id="independent-run-001", registered_at="2026-08-15T09:01:00+08:00",
            execution_started_at="2026-08-15T09:02:00+08:00",
        )
        self.assertEqual(registry["attempt_number"], 1)
        with self.assertRaisesRegex(self.module.ProtocolError, "after freeze and before execution"):
            self.module.build_first_attempt_registry(
                lock, run_id="late", registered_at="2026-08-15T09:03:00+08:00",
                execution_started_at="2026-08-15T09:02:00+08:00",
            )
        with self.assertRaisesRegex(self.module.ProtocolError, "strictly after"):
            self.module.build_allocation_reveal(
                document, secret=secret, nonce=nonce, freeze_id=freeze_id,
                ratings_completed_at="2026-08-15T12:00:00+08:00", revealed_at="2026-08-15T11:59:00+08:00",
                ratings_lock=self.ratings_lock(document),
            )

    def test_response_and_rating_hash_reuse_attacks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            rows = []
            for label in self.module.LABELS:
                path = base / f"{label}.md"
                path.write_text(f"distinct response {label}\n", encoding="utf-8")
                rows.append({"skill_id": "zju-literature-search", "case_id": "HLD-00", "blind_label": label, "call_id": f"call-{label}", "response_path": str(path)})
            manifest = self.module.build_response_manifest(
                rows, run_id="independent-run-001", layer=self.task_document(1)["layer"]
            )
            self.assertEqual(len(manifest["responses"]), 3)
            (base / "C.md").write_text("distinct response A\n", encoding="utf-8")
            with self.assertRaisesRegex(self.module.ProtocolError, "response hashes must differ"):
                self.module.build_response_manifest(
                    rows, run_id="independent-run-001", layer=self.task_document(1)["layer"]
                )

    def test_rater_packet_contains_no_arm_or_skill_identity(self):
        document = self.task_document(1)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            rows = []
            for label in self.module.LABELS:
                path = base / f"{label}.md"; path.write_text(f"response {label}", encoding="utf-8")
                rows.append({"skill_id": "zju-literature-search", "case_id": "HLD-00", "blind_label": label, "call_id": f"call-{label}", "response_path": str(path)})
            manifest = self.module.build_response_manifest(
                rows, run_id="independent-run-001", layer=document["layer"]
            )
            packet = self.module.build_rater_packet(document, manifest, rater_id="rater-one", rubric_sha256="a" * 64)
            encoded = json.dumps(packet)
            self.assertTrue(packet["double_blind"])
            self.assertNotIn("distilled_skill", encoded)
            self.assertNotIn("nature-academic-search", encoded)
            self.assertNotIn("zju-literature-search", encoded)

    def test_two_rater_three_arm_aggregate_uses_paired_stronger_control(self):
        document = self.task_document(3)
        secret, nonce, freeze_id = "independent-secret-0123456789abcdef", "nonce-0123456789", "freeze-001"
        reveal = self.module.build_allocation_reveal(
            document, secret=secret, nonce=nonce, freeze_id=freeze_id,
            ratings_completed_at="2026-08-15T12:00:00+08:00", revealed_at="2026-08-15T12:01:00+08:00",
            ratings_lock=self.ratings_lock(document),
        )
        rows = []
        arm_by_label = {(row["case_id"], row["blind_label"]): row["arm"] for row in reveal["assignments"]}
        for case in document["cases"]:
            for label in self.module.LABELS:
                arm = arm_by_label[(case["case_id"], label)]
                score = {"no_skill": 50, "strongest_open_source_baseline": 60, "distilled_skill": 80}[arm]
                digest = self.module.canonical_sha256({"case": case["case_id"], "label": label})
                for rater in ("r1", "r2"):
                    rows.append({"layer": document["layer"], "skill_id": case["skill_id"], "case_id": case["case_id"], "blind_label": label, "call_id": f"{rater}-{case['case_id']}-{label}", "response_sha256": digest, "score": score})
        result = self.module.aggregate_three_arm(rows, reveal, iterations=500)
        skill = result["skills"]["zju-literature-search"]
        self.assertEqual(skill["paired_gain"]["mean_gain"], 20)
        self.assertGreater(skill["paired_gain"]["lower"], 0)
        forged = copy.deepcopy(rows); forged[1]["call_id"] = forged[0]["call_id"]
        with self.assertRaisesRegex(self.module.ProtocolError, "call IDs"):
            self.module.aggregate_three_arm(forged, reveal, iterations=20)

    def test_ed25519_signature_detects_bundle_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            private_path = Path(temporary) / "admin-private.pem"
            registration = self.module.generate_ed25519_key(private_path, key_id="independent-admin-1")
            bundle = {"schema_version": "3.0", "verification_id": "VERIFY-1", "payload": {"first_attempt": True}}
            signed = self.module.sign_verification_bundle(bundle, private_path)
            self.assertTrue(self.module.verify_signature(signed, registration["public_key_ed25519_hex"]))
            signed["payload"]["first_attempt"] = False
            self.assertFalse(self.module.verify_signature(signed, registration["public_key_ed25519_hex"]))

    def test_post_signature_result_rating_and_artifact_replacement_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            private_path = Path(temporary) / "admin-private.pem"
            registration = self.module.generate_ed25519_key(private_path, key_id="independent-admin-2")
            final_bundle = {
                "schema_version": "3.0",
                "portfolio_results_sha256": "1" * 64,
                "portfolio_results_file_sha256": "9" * 64,
                "ratings_locks": [{"ratings_lock_sha256": "2" * 64}],
                "artifact_manifests": [{"artifact_manifest_sha256": "3" * 64}],
            }
            signed = self.module.sign_verification_bundle(final_bundle, private_path)
            for mutate in (
                lambda value: value.__setitem__("portfolio_results_sha256", "4" * 64),
                lambda value: value.__setitem__("portfolio_results_file_sha256", "8" * 64),
                lambda value: value["ratings_locks"][0].__setitem__("ratings_lock_sha256", "5" * 64),
                lambda value: value["artifact_manifests"][0].__setitem__("artifact_manifest_sha256", "6" * 64),
            ):
                forged = copy.deepcopy(signed)
                mutate(forged)
                self.assertFalse(
                    self.module.verify_signature(forged, registration["public_key_ed25519_hex"])
                )

    def test_artifact_manifest_hashes_fixture_bytes_and_detects_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture.bin"
            fixture.write_bytes(b"frozen fixture bytes")
            manifest = self.module.build_artifact_manifest([{
                "skill_id": "zju-literature-search", "case_id": "SAME-ID",
                "blind_label": None, "artifact_role": "fixture", "artifact_path": str(fixture),
            }], run_id="independent-run-001", layer=self.module.TASK_LAYERS[1])
            self.module._verify_manifest_files(
                manifest, collection="artifacts", path_field="artifact_path",
                hash_field="artifact_sha256", size_field="artifact_size_bytes",
            )
            fixture.write_bytes(b"replacement fixture bytes")
            with self.assertRaisesRegex(self.module.ProtocolError, "changed after hashing"):
                self.module._verify_manifest_files(
                    manifest, collection="artifacts", path_field="artifact_path",
                    hash_field="artifact_sha256", size_field="artifact_size_bytes",
                )

    def test_cross_layer_same_case_id_cannot_share_an_assignment_key(self):
        l4 = self.task_document(1)
        l3 = copy.deepcopy(l4)
        l3["layer"] = self.module.TASK_LAYERS[0]
        l3["cases"][0]["stratum"] = "nominal"
        secret, nonce = "independent-secret-0123456789abcdef", "nonce-0123456789"
        l3_rows = self.module.latin_square_assignments(
            l3, secret=secret, nonce=nonce, freeze_id="freeze-l3"
        )
        l4_rows = self.module.latin_square_assignments(
            l4, secret=secret, nonce=nonce, freeze_id="freeze-l4"
        )
        keys = {
            (row["layer"], row["skill_id"], row["case_id"], row["arm"])
            for row in l3_rows + l4_rows
        }
        self.assertEqual(len(keys), 6)
        l3_lock = self.ratings_lock(l3)
        l3_reveal = self.module.build_allocation_reveal(
            l3, secret=secret, nonce=nonce, freeze_id="freeze-l3",
            ratings_completed_at="2026-08-15T12:00:00+08:00",
            revealed_at="2026-08-15T12:01:00+08:00", ratings_lock=l3_lock,
        )
        wrong_layer_rating = [{
            "layer": self.module.TASK_LAYERS[1], "skill_id": "zju-literature-search",
            "case_id": "HLD-00", "blind_label": "A", "call_id": "wrong-layer-call",
            "response_sha256": "a" * 64, "score": 50,
        }]
        with self.assertRaisesRegex(self.module.ProtocolError, "layer does not match"):
            self.module.aggregate_three_arm(wrong_layer_rating, l3_reveal, iterations=10)

    def test_assembler_verifies_complete_hash_chain_before_signing(self):
        document = self.task_document(1)
        run_id = "independent-run-001"
        secret, nonce, freeze_id = "independent-secret-0123456789abcdef", "nonce-0123456789", "freeze-001"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fixture_path = base / "fixture.json"
            fixture_path.write_bytes(self.module.canonical_bytes(document["cases"][0]["fixture"]))
            document["cases"][0]["fixture_files"] = [str(fixture_path)]
            commitment = self.module.allocation_commitment(
                secret, nonce, freeze_id, document["layer"]
            )
            lock = self.module.build_holdout_lock(
                document, freeze_id=freeze_id, frozen_at="2026-08-15T09:00:00+08:00",
                commitment_sha256=commitment, matrix=self.matrix, protocol=self.protocol,
                require_full_portfolio=False,
            )
            first = self.module.build_first_attempt_registry(
                lock, run_id=run_id, registered_at="2026-08-15T09:01:00+08:00",
                execution_started_at="2026-08-15T09:02:00+08:00",
            )
            response_rows, artifact_rows, event_rows = [], [{
                "skill_id": "zju-literature-search", "case_id": "HLD-00",
                "blind_label": None, "artifact_role": "fixture", "artifact_path": str(fixture_path),
            }], []
            for label in self.module.LABELS:
                response_path = base / f"response-{label}.md"
                response_path.write_text(f"bounded response {label}\n", encoding="utf-8")
                output_path = base / f"artifact-{label}.txt"
                output_path.write_text(f"artifact {label}\n", encoding="utf-8")
                response_rows.append({
                    "skill_id": "zju-literature-search", "case_id": "HLD-00",
                    "blind_label": label, "call_id": f"model-call-{label}",
                    "model": "fixed-model-v1", "response_path": str(response_path),
                })
                artifact_rows.append({
                    "skill_id": "zju-literature-search", "case_id": "HLD-00",
                    "blind_label": label, "artifact_role": "generated_artifact",
                    "artifact_path": str(output_path),
                })
                event_rows.append({
                    "event_id": f"event-{label}", "skill_id": "zju-literature-search",
                    "case_id": "HLD-00", "blind_label": label, "call_id": f"model-call-{label}",
                    "event_type": "model_response_completed", "occurred_at": "2026-08-15T10:00:00+08:00",
                    "tool_policy_sha256": "7" * 64,
                    "payload_sha256": self.module.canonical_sha256({"label": label}),
                })
            response_manifest = self.module.build_response_manifest(
                response_rows, run_id=run_id, layer=document["layer"]
            )
            artifact_manifest = self.module.build_artifact_manifest(
                artifact_rows, run_id=run_id, layer=document["layer"]
            )
            event_manifest = self.module.build_execution_event_manifest(
                event_rows, run_id=run_id, layer=document["layer"]
            )
            preliminary_lock = self.module.build_ratings_lock(
                self.rating_records(document), run_id=run_id, layer=document["layer"],
                sealed_at="2026-08-15T11:58:00+08:00",
                response_manifest_sha256=response_manifest["response_manifest_sha256"],
            )
            reveal = self.module.build_allocation_reveal(
                document, secret=secret, nonce=nonce, freeze_id=freeze_id,
                ratings_completed_at="2026-08-15T12:00:00+08:00",
                revealed_at="2026-08-15T12:01:00+08:00", ratings_lock=preliminary_lock,
            )
            arm_by_label = {row["blind_label"]: row["arm"] for row in reveal["assignments"]}
            response_by_label = {row["blind_label"]: row for row in response_manifest["responses"]}
            artifact_by_label = {
                row["blind_label"]: row for row in artifact_manifest["artifacts"]
                if row["artifact_role"] == "generated_artifact"
            }
            rating_by_label = {
                row["blind_label"]: row for row in self.rating_records(document)
            }
            fixture_hashes = [self.module.sha256_file(fixture_path)]
            records = []
            for label in self.module.LABELS:
                rating_row = rating_by_label[label]
                bound_ratings = copy.deepcopy(rating_row["primary_ratings"])
                for rating in bound_ratings:
                    rating["response_sha256"] = response_by_label[label]["response_sha256"]
                records.append({
                    "layer": document["layer"], "skill_id": "zju-literature-search",
                    "case_id": "HLD-00", "blind_label": label, "arm": arm_by_label[label],
                    "response_sha256": response_by_label[label]["response_sha256"],
                    "fixture_sha256": self.module.canonical_sha256(fixture_hashes),
                    "artifact_sha256s": [artifact_by_label[label]["artifact_sha256"]],
                    "primary_ratings": bound_ratings, "adjudication": None,
                })
            ratings_lock = self.module.build_ratings_lock(
                records, run_id=run_id, layer=document["layer"],
                sealed_at="2026-08-15T11:58:00+08:00",
                response_manifest_sha256=response_manifest["response_manifest_sha256"],
            )
            reveal = self.module.build_allocation_reveal(
                document, secret=secret, nonce=nonce, freeze_id=freeze_id,
                ratings_completed_at="2026-08-15T12:00:00+08:00",
                revealed_at="2026-08-15T12:01:00+08:00", ratings_lock=ratings_lock,
            )
            baseline = self.module.build_baseline_selection({
                "zju-literature-search": {
                    "baseline_id": "nature-academic-search", "source_commit": "a" * 40,
                    "baseline_packet_sha256": "b" * 64,
                }
            }, selected_at="2026-08-15T08:00:00+08:00", matrix=self.matrix)
            rater_precommit = self.module.build_rater_precommit(
                ["r1", "r2"], adjudicator_id="r3", rubric_sha256="c" * 64
            )
            run_manifest = self.module.build_run_manifest(
                run_id=run_id, skill_commit="d" * 40, baseline_selection=baseline,
                rater_precommit=rater_precommit, holdout_lock=lock,
                first_attempt_registry=first, execution_started_at="2026-08-15T09:02:00+08:00",
                protocol=self.protocol, matrix=self.matrix,
            )
            portfolio_results = {"run_id": run_id, "records": records}
            results_path = base / "portfolio-results.json"
            self.module.write_json(results_path, portfolio_results)
            components_hash = self.module._evidence_component_hash(
                run_manifest=run_manifest, response_manifests=[response_manifest],
                artifact_manifests=[artifact_manifest], ratings_locks=[ratings_lock],
                execution_event_manifests=[event_manifest], allocation_reveals=[reveal],
            )
            attestation = {
                "run_id": run_id, "attestor_id": "external-admin",
                "independent_of_skill_authors": True, "first_attempt_confirmed": True,
                "tool_policy_parity_verified": True, "evidence_bundle_sha256": components_hash,
            }
            bundle = self.module.assemble_verification_bundle(
                verification_id="verify-001", verified_at="2026-08-15T12:05:00+08:00",
                verifier_id="external-admin", verification_key_id="external-key-1",
                run_manifest=run_manifest, holdout_lock=lock, first_attempt_registry=first,
                allocation_reveals=[reveal], response_manifests=[response_manifest],
                artifact_manifests=[artifact_manifest], ratings_locks=[ratings_lock],
                execution_event_manifests=[event_manifest], portfolio_results=portfolio_results,
                portfolio_results_file=results_path,
                administration_attestation=attestation,
            )
            self.assertEqual(
                bundle["portfolio_results_sha256"], self.module.canonical_sha256(portfolio_results)
            )
            self.assertEqual(
                bundle["portfolio_results_file_sha256"], self.module.sha256_file(results_path)
            )
            forged_results = copy.deepcopy(portfolio_results)
            forged_results["records"][0]["primary_ratings"][0]["score"] = 10
            with self.assertRaisesRegex(self.module.ProtocolError, "ratings or adjudication differ"):
                self.module.assemble_verification_bundle(
                    verification_id="verify-002", verified_at="2026-08-15T12:05:00+08:00",
                    verifier_id="external-admin", verification_key_id="external-key-1",
                    run_manifest=run_manifest, holdout_lock=lock, first_attempt_registry=first,
                    allocation_reveals=[reveal], response_manifests=[response_manifest],
                    artifact_manifests=[artifact_manifest], ratings_locks=[ratings_lock],
                    execution_event_manifests=[event_manifest], portfolio_results=forged_results,
                    portfolio_results_file=None,
                    administration_attestation=attestation,
                )

    def test_baseline_selection_must_be_predeclared_and_commit_pinned(self):
        choices = {"zju-literature-search": {"baseline_id": "nature-academic-search", "source_commit": "a" * 40, "baseline_packet_sha256": "b" * 64}}
        selected = self.module.build_baseline_selection(choices, selected_at="2026-08-15T08:00:00+08:00", matrix=self.matrix)
        self.assertRegex(selected["zju-literature-search"]["selection_lock_sha256"], r"^[0-9a-f]{64}$")
        choices["zju-literature-search"]["baseline_id"] = "chosen-after-seeing-results"
        with self.assertRaisesRegex(self.module.ProtocolError, "not a matrix candidate"):
            self.module.build_baseline_selection(choices, selected_at="2026-08-15T08:00:00+08:00", matrix=self.matrix)


if __name__ == "__main__":
    unittest.main()
