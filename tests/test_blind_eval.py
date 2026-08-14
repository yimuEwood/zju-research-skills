from __future__ import annotations

import importlib.util
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class BlindEvaluationTests(unittest.TestCase):
    def test_runtime_env_parses_wininet_proxy_without_credentials(self):
        module = load_module("runtime_env_test", "evals/runtime_env.py")
        self.assertEqual(
            module.parse_proxy_server("127.0.0.1:7890"),
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        )
        self.assertEqual(
            module.parse_proxy_server("http=127.0.0.1:7890;https=127.0.0.1:7891"),
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7891"},
        )
        self.assertEqual(module.parse_proxy_server("user:password@127.0.0.1:7890"), {})

    def test_upstream_cache_matches_tracked_lock(self):
        module = load_module("prepare_upstream_test", "evals/prepare_upstream.py")
        manifest_path = module.DEFAULT_CACHE / "prepared-manifest.json"
        if not manifest_path.is_file():
            self.skipTest("optional pinned upstream cache is not present in a clean checkout")
        result = module.verify(module.DEFAULT_CACHE, ROOT / "evals/upstream-lock.json")
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["files"], 306)

    def test_plan_keeps_arm_evidence_private(self):
        module = load_module("blind_eval_plan_test", "evals/blind_eval.py")
        upstream_lock = json.loads((ROOT / "evals/upstream-lock.json").read_text(encoding="utf-8"))

        def materialize_test_instructions(case, arm, destination, config):
            del case, config
            if arm == "no_skill":
                return []
            target = destination / "instructions/set-01"
            target.mkdir(parents=True, exist_ok=False)
            (target / "SKILL.md").write_text(
                "---\nname: test-instruction\ndescription: deterministic test fixture\n---\n",
                encoding="utf-8",
            )
            return [{"set": target.name, "sha256": module.hash_tree(target)}]

        with tempfile.TemporaryDirectory() as temp_dir:
            module.RESULTS = Path(temp_dir) / "results"
            with (
                mock.patch.object(module, "codex_version", return_value="codex-cli test"),
                mock.patch.object(module, "verify_upstream", return_value=upstream_lock),
                mock.patch.object(module, "copy_instruction_set", side_effect=materialize_test_instructions),
            ):
                manifest = module.create_plan("unit-plan", "pilot", ["FULL-06"])
            self.assertEqual(manifest["task_count"], 3)
            self.assertTrue(all(set(task) == {"case_id", "blind_label", "status"} for task in manifest["tasks"]))
            private = json.loads((module.RESULTS / "unit-plan/private/arm-map.json").read_text(encoding="utf-8"))
            self.assertIn("arm_map", private)
            self.assertIn("instruction_sets", private["tasks"][0])
            public_text = (module.RESULTS / "unit-plan/run-manifest.json").read_text(encoding="utf-8")
            self.assertNotIn("no_skill", public_text)
            self.assertNotIn("upstream_skill", public_text)
            self.assertNotIn("distilled_skill", public_text)

    def test_usage_parser_uses_last_usage_object(self):
        module = load_module("blind_eval_usage_test", "evals/blind_eval.py")
        events = "\n".join([
            json.dumps({"usage": {"input_tokens": 10, "output_tokens": 2}}),
            json.dumps({"result": {"usage": {"input_tokens": 30, "cached_input_tokens": 5, "output_tokens": 8}}}),
        ])
        self.assertEqual(module.parse_usage(events), {"input_tokens": 30, "cached_input_tokens": 5, "output_tokens": 8})

    def test_instruction_read_audit_requires_every_skill_entrypoint(self):
        module = load_module("blind_eval_instruction_audit_test", "evals/blind_eval.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            for name in ("set-01", "set-02"):
                skill = task_dir / "work/instructions" / name / "SKILL.md"
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")

            def event(command: str) -> str:
                return json.dumps({
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": command, "status": "completed", "exit_code": 0},
                })

            partial = module.instruction_read_audit(task_dir, event("Get-Content instructions/set-01/SKILL.md"))
            self.assertFalse(partial["verified"])
            self.assertEqual(partial["missing"], ["instructions/set-02/skill.md"])
            complete = module.instruction_read_audit(
                task_dir,
                "\n".join([
                    event("Get-Content instructions/set-01/SKILL.md"),
                    event("Get-Content instructions/set-02/SKILL.md"),
                ]),
            )
            self.assertTrue(complete["verified"])

    def test_leakage_markers_cover_all_instruction_names(self):
        module = load_module("blind_eval_leak_test", "evals/blind_eval.py")
        config = json.loads((ROOT / "evals/experiment.json").read_text(encoding="utf-8"))
        markers = module.leakage_markers(config)
        self.assertIn("nature-downloader", markers)
        self.assertIn("zju-fulltext-access", markers)
        self.assertEqual(len(markers), 17)

    def test_aggregate_passes_complete_synthetic_full_run(self):
        module = load_module("aggregate_blind_eval_test", "evals/aggregate_blind_eval.py")
        cases = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))["cases"]
        with tempfile.TemporaryDirectory() as temp_dir:
            module.RESULTS = Path(temp_dir) / "results"
            run_dir = module.RESULTS / "synthetic-full"
            run_dir.mkdir(parents=True)
            (run_dir / "run-manifest.json").write_text(json.dumps({"case_ids": [case["id"] for case in cases]}), encoding="utf-8")
            arm_map = {case["id"]: {"A": "no_skill", "B": "upstream_skill", "C": "distilled_skill"} for case in cases}
            (run_dir / "private").mkdir()
            (run_dir / "private/arm-map.json").write_text(json.dumps({"arm_map": arm_map}), encoding="utf-8")
            score_by_label = {"A": 2, "B": 3, "C": 4}
            for case in cases:
                for label in ("A", "B", "C"):
                    execution_dir = run_dir / "tasks" / case["id"] / label
                    execution_dir.mkdir(parents=True)
                    execution = {"duration_seconds": 8 if label == "C" else 10, "usage": {"input_tokens": 70 if label == "C" else 90, "output_tokens": 10}}
                    (execution_dir / "execution.json").write_text(json.dumps(execution), encoding="utf-8")
                for rater in ("r1", "r2"):
                    rating_dir = run_dir / "ratings" / rater
                    rating_dir.mkdir(parents=True, exist_ok=True)
                    ratings = []
                    for label in ("A", "B", "C"):
                        score = score_by_label[label]
                        ratings.append({
                            "blind_label": label,
                            "scores": {name: score for name in module.DIMENSION_WEIGHTS},
                            "critical_failure": False,
                            "failure_reason": "",
                            "gold_checks": [],
                            "rationale": "synthetic test",
                        })
                    (rating_dir / f"{case['id']}.json").write_text(json.dumps({"case_id": case["id"], "ratings": ratings}), encoding="utf-8")
            result = module.aggregate("synthetic-full", ["r1", "r2"])
            self.assertTrue(result["complete"], result)
            self.assertTrue(result["release_gate_passed"], result)
            self.assertTrue(all(item["passed"] for item in result["skill_results"].values()))

    def test_v2_current_status_refuses_release_without_new_holdout(self):
        module = load_module("protocol_v2_status_test", "evals/protocol_v2.py")
        config = json.loads((ROOT / "evals/protocol-v2.json").read_text(encoding="utf-8"))
        status = module.protocol_status(config, ROOT)
        self.assertFalse(status["holdout_ready"], status)
        self.assertFalse(status["execution_ready"], status)
        self.assertFalse(status["release_gate_passed"], status)
        self.assertFalse(status["new_v2_score_available"])
        self.assertEqual(status["known_case_count"], 70)
        self.assertIn("frozen-holdout-not-configured", status["frozen_holdout"]["issues"])
        self.assertIn("validated-aggregate-not-configured", status["validated_aggregate"]["issues"])
        self.assertTrue(status["legacy_results_excluded"])
        completed = subprocess.run(
            [sys.executable, str(ROOT / "evals/protocol_v2.py"), "status"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        pinned = copy.deepcopy(config)
        pinned["dataset"]["validated_aggregate_path"] = "evals/result.json"
        pinned["dataset"]["validated_aggregate_sha256"] = "a" * 64
        self.assertEqual(
            module.protocol_config_sha256(config),
            module.protocol_config_sha256(pinned),
        )
        escaping = copy.deepcopy(config)
        escaping["dataset"]["development_cases_path"] = "../outside.json"
        with self.assertRaisesRegex(module.ProtocolError, "escapes the repository root"):
            module.protocol_status(escaping, ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            (base / "development.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
            (base / "run").mkdir()
            forged_config = copy.deepcopy(config)
            forged_config["dataset"].update({
                "development_cases_path": "development.json",
                "known_case_sources": [{"kind": "ids", "case_ids": [], "reason": "test"}],
                "validated_aggregate_path": "forged.json",
                "validated_run_dir_path": "run",
                "precommitted_rater_ids": ["r1", "r2"],
            })
            forged = {
                "protocol_version": "2.0",
                "protocol_id": forged_config["protocol_id"],
                "protocol_config_sha256": module.protocol_config_sha256(forged_config),
                "complete": True,
                "release_gate_passed": True,
                "evidence_integrity": {"valid": True},
                "dataset_audit": {},
                "first_attempt_registry_audit": {},
                "allocation_reveal_audit": {"verified": True},
                "rater_ids": ["r1", "r2"],
                "adjudicator_id": None,
            }
            forged_path = base / "forged.json"
            forged_path.write_text(json.dumps(forged), encoding="utf-8")
            forged_config["dataset"]["validated_aggregate_sha256"] = module.sha256_file(forged_path)
            forged_status = module.protocol_status(forged_config, base)
            self.assertFalse(forged_status["release_gate_passed"], forged_status)
            self.assertIn(
                "validated-aggregate-holdout-not-eligible",
                forged_status["validated_aggregate"]["issues"],
            )

    def test_v2_frozen_holdout_without_validated_aggregate_is_not_release_passed(self):
        module = load_module("protocol_v2_status_split_test", "evals/protocol_v2.py")
        config = copy.deepcopy(json.loads((ROOT / "evals/protocol-v2.json").read_text(encoding="utf-8")))
        case_document = {
            "schema_version": "2.0",
            "split": "frozen_holdout",
            "cases": [{
                "id": "STATUS-01", "skill": "skill-one", "domain": "test",
                "prompt": "Fresh status task", "fixture": {}, "evaluation_status": "unseen_holdout",
                "gold_checks": [{"id": "G1", "criterion": "Required field"}],
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            cases_path = base / "holdout.json"
            lock_path = base / "holdout.lock.json"
            registry_path = base / "first-attempt.json"
            development_path = base / "development.json"
            cases_path.write_text(json.dumps(case_document), encoding="utf-8")
            development_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
            lock = module.build_holdout_lock(
                cases_path,
                protocol_id=config["protocol_id"],
                freeze_id="status-freeze-001",
                allocation_commitment_sha256="e" * 64,
                frozen_at="2026-08-11T00:00:00+00:00",
            )
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            registry_path.write_text(json.dumps(module.build_first_attempt_registry(
                lock, run_id="status-run", registered_at="2026-08-11T00:00:30+00:00"
            )), encoding="utf-8")
            config["dataset"].update({
                "development_cases_path": development_path.name,
                "frozen_holdout_cases_path": cases_path.name,
                "frozen_holdout_lock_path": lock_path.name,
                "first_attempt_registry_path": registry_path.name,
                "precommitted_rater_ids": ["r1", "r2"],
                "known_case_sources": [{"kind": "ids", "case_ids": [], "reason": "test"}],
                "pilot_case_ids": [],
            })
            status = module.protocol_status(config, base)
            self.assertTrue(status["holdout_ready"], status)
            self.assertTrue(status["execution_ready"], status)
            self.assertFalse(status["release_gate_passed"], status)
            missing_development = copy.deepcopy(config)
            missing_development["dataset"]["development_cases_path"] = "missing-development.json"
            missing_status = module.protocol_status(missing_development, base)
            self.assertFalse(missing_status["holdout_ready"], missing_status)
            self.assertFalse(missing_status["execution_ready"], missing_status)
            self.assertIn(
                "development-case-source-missing",
                missing_status["frozen_holdout"]["issues"],
            )

    def test_v2_holdout_freeze_rejects_known_and_pilot_cases(self):
        module = load_module("protocol_v2_freeze_reject_test", "evals/protocol_v2.py")
        document = {
            "schema_version": "2.0",
            "split": "frozen_holdout",
            "cases": [{
                "id": "NEW-01",
                "skill": "skill-one",
                "domain": "test",
                "prompt": "A new prompt",
                "fixture": {},
                "evaluation_status": "unseen_holdout",
                "gold_checks": [{"id": "G1", "criterion": "Required field is present"}],
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cases_path = Path(temp_dir) / "holdout.json"
            cases_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(module.ProtocolError, "pilot-case-in-holdout"):
                module.build_holdout_lock(
                    cases_path,
                    protocol_id="test-v2",
                    freeze_id="freeze-001",
                    allocation_commitment_sha256="a" * 64,
                    pilot_case_ids={"NEW-01"},
                )
            with self.assertRaisesRegex(module.ProtocolError, "known-case-in-holdout"):
                module.build_holdout_lock(
                    cases_path,
                    protocol_id="test-v2",
                    freeze_id="freeze-001",
                    allocation_commitment_sha256="a" * 64,
                    known_case_ids={"NEW-01"},
                )
            known_fingerprint = module.case_fingerprint(document["cases"][0])
            document["cases"][0]["id"] = "RENAMED-01"
            document["cases"][0]["gold_checks"][0]["id"] = "G2"
            self.assertEqual(known_fingerprint, module.case_fingerprint(document["cases"][0]))
            cases_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(module.ProtocolError, "known-case-fingerprint-in-holdout"):
                module.build_holdout_lock(
                    cases_path,
                    protocol_id="test-v2",
                    freeze_id="freeze-001",
                    allocation_commitment_sha256="a" * 64,
                    known_case_fingerprints={known_fingerprint},
                )

    def test_v2_holdout_lock_detects_post_freeze_change(self):
        module = load_module("protocol_v2_freeze_hash_test", "evals/protocol_v2.py")
        document = {
            "schema_version": "2.0",
            "split": "frozen_holdout",
            "cases": [{
                "id": "NEW-02",
                "skill": "skill-one",
                "domain": "test",
                "prompt": "Original prompt",
                "fixture": {},
                "evaluation_status": "unseen_holdout",
                "gold_checks": [{"id": "G1", "criterion": "Required field is present"}],
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cases_path = Path(temp_dir) / "holdout.json"
            lock_path = Path(temp_dir) / "holdout.lock.json"
            cases_path.write_text(json.dumps(document), encoding="utf-8")
            lock = module.build_holdout_lock(
                cases_path,
                protocol_id="test-v2",
                freeze_id="freeze-002",
                allocation_commitment_sha256="b" * 64,
                frozen_at="2026-08-11T00:00:00+00:00",
            )
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            clean = module.audit_holdout(cases_path, lock_path, protocol_id="test-v2")
            self.assertTrue(clean["eligible"], clean)
            document["cases"][0]["prompt"] = "Changed after inspection"
            cases_path.write_text(json.dumps(document), encoding="utf-8")
            changed = module.audit_holdout(cases_path, lock_path, protocol_id="test-v2")
            self.assertFalse(changed["eligible"])
            self.assertIn("holdout-case-document-hash-mismatch", changed["issues"])

            cases_path.write_text(json.dumps({**document, "cases": [{**document["cases"][0], "prompt": "Original prompt"}]}), encoding="utf-8")
            registry = module.build_first_attempt_registry(
                lock,
                run_id="first-run",
                registered_at="2026-08-11T00:00:30+00:00",
            )
            registry_path = Path(temp_dir) / "attempt-registry.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            registry_audit = module.audit_first_attempt_registry(
                registry_path,
                lock,
                expected_run_id="first-run",
                expected_sha256=module.sha256_file(registry_path),
            )
            self.assertTrue(registry_audit["eligible"], registry_audit)
            early_registry = copy.deepcopy(registry)
            early_registry["registered_at"] = "2026-08-10T23:59:59+00:00"
            registry_path.write_text(json.dumps(early_registry), encoding="utf-8")
            early = module.audit_first_attempt_registry(registry_path, lock)
            self.assertFalse(early["eligible"])
            self.assertIn("first-attempt-registry-predates-holdout-freeze", early["issues"])
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            registry["attempts"].append(copy.deepcopy(registry["attempts"][0]))
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            repeated = module.audit_first_attempt_registry(registry_path, lock, expected_run_id="first-run")
            self.assertFalse(repeated["eligible"])
            self.assertIn("first-attempt-registry-must-contain-exactly-one-attempt", repeated["issues"])

    def test_v2_arm_allocation_requires_private_committed_secret(self):
        module = load_module("protocol_v2_allocation_test", "evals/protocol_v2.py")
        cases = [
            {"id": f"ONE-{index:02d}", "skill": "skill-one"} for index in range(10)
        ] + [
            {"id": f"TWO-{index:02d}", "skill": "skill-two"} for index in range(10)
        ]
        secret = "private-allocation-secret-0000000001"
        nonce = "private-nonce-001"
        kwargs = {"protocol_id": "test-v2", "freeze_id": "freeze-003"}
        first = module.balanced_arm_map(cases, secret, nonce, **kwargs)
        second = module.balanced_arm_map(list(reversed(cases)), secret, nonce, **kwargs)
        different_seed = module.balanced_arm_map(cases, secret, "private-nonce-002", **kwargs)
        self.assertEqual(first, second)
        self.assertNotEqual(first, different_seed)
        config = json.loads((ROOT / "evals/protocol-v2.json").read_text(encoding="utf-8"))
        self.assertNotIn("seed", config["allocation"])
        commitment = module.allocation_commitment(secret, nonce, **kwargs)
        lock = {**kwargs, "allocation_commitment_sha256": commitment}
        reveal = {
            "schema_version": "2.0",
            "protocol_id": "test-v2",
            "freeze_id": "freeze-003",
            "secret": secret,
            "nonce": nonce,
            "commitment_sha256": commitment,
            "ratings_bundle_sha256": "c" * 64,
            "revealed_at": "2026-08-11T00:04:00+00:00",
        }
        self.assertTrue(module.verify_allocation_reveal(
            reveal, lock, cases, first, ratings_bundle_sha256="c" * 64
        )["verified"])
        unexpected = {**reveal, "self_reported_valid": True}
        with self.assertRaisesRegex(module.ProtocolError, "schema validation failed"):
            module.verify_allocation_reveal(
                unexpected, lock, cases, first, ratings_bundle_sha256="c" * 64
            )
        reveal["secret"] = "wrong-allocation-secret-00000000000"
        with self.assertRaisesRegex(module.ProtocolError, "does not open"):
            module.verify_allocation_reveal(
                reveal, lock, cases, first, ratings_bundle_sha256="c" * 64
            )
        balance = module.allocation_balance(cases, first)
        self.assertTrue(balance["valid"], balance)
        for skill in balance["skills"].values():
            self.assertLessEqual(skill["maximum_imbalance"], 1)
            for counts in skill["label_arm_counts"].values():
                self.assertEqual(sorted(counts.values()), [3, 3, 4])

    def test_v2_event_scan_rejects_web_mcp_and_network_commands(self):
        module = load_module("protocol_v2_event_scan_test", "evals/protocol_v2.py")
        events = "\n".join([
            json.dumps({
                "type": "item.started",
                "item": {"id": "web-1", "type": "web_search", "query": "example"},
            }),
            json.dumps({
                "type": "item.completed",
                "item": {"id": "web-1", "type": "web_search", "query": "example"},
            }),
            json.dumps({
                "type": "item.completed",
                "item": {"id": "mcp-1", "type": "mcp_tool_call", "server": "codex_apps"},
            }),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "cmd-1",
                    "type": "command_execution",
                    "command": 'powershell.exe -Command "curl https://example.org/paper"',
                },
            }),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "cmd-2",
                    "type": "command_execution",
                    "command": 'powershell.exe -Command "Get-Content fixture.json"',
                },
            }),
            json.dumps({"type": "turn.completed", "status": "completed", "turn_id": "turn-1"}),
        ])
        result = module.scan_events_text(events)
        self.assertFalse(result["compliant"])
        self.assertEqual(result["protocol_deviation_count"], 3, result)
        self.assertEqual(
            {item["code"] for item in result["violations"]},
            {"web-tool-call", "mcp-tool-call", "network-command"},
        )

    def test_v2_event_scan_requires_parseable_log_and_allows_local_reads(self):
        module = load_module("protocol_v2_event_clean_test", "evals/protocol_v2.py")
        clean = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "cmd-local",
                    "type": "command_execution",
                    "command": 'powershell.exe -Command "Get-Content fixture.json"',
                },
            }),
            json.dumps({"type": "turn.completed", "status": "completed", "turn_id": "turn-clean"}),
        ])
        self.assertTrue(module.scan_events_text(clean)["compliant"])
        malformed = module.scan_events_text(clean + "\n{not-json")
        self.assertFalse(malformed["compliant"])
        self.assertEqual(malformed["violations"][0]["code"], "malformed-event")
        self.assertIn("events-log-empty", {item["code"] for item in module.scan_events_text("")["violations"]})
        self.assertIn("event-not-object", {item["code"] for item in module.scan_events_text("[]")["violations"]})
        no_terminal = module.scan_events_text(json.dumps({"type": "item.completed", "item": {"id": "x"}}))
        self.assertIn("trusted-terminal-event-missing", {item["code"] for item in no_terminal["violations"]})
        failed_after_success = module.scan_events_text(clean + "\n" + json.dumps({
            "type": "response.failed", "status": "failed", "response_id": "response-failed"
        }))
        self.assertFalse(failed_after_success["compliant"])
        self.assertIn(
            "terminal-sequence-invalid",
            {item["code"] for item in failed_after_success["violations"]},
        )
        duplicate_success = module.scan_events_text(clean + "\n" + json.dumps({
            "type": "response.completed", "status": "completed", "response_id": "response-2"
        }))
        self.assertIn(
            "multiple-trusted-terminal-events",
            {item["code"] for item in duplicate_success["violations"]},
        )

    def test_v2_gold_checks_must_match_one_to_one(self):
        module = load_module("protocol_v2_gold_test", "evals/protocol_v2.py")
        expected = ["G1", "G2"]
        valid = [
            {"check_id": "G2", "met": False, "evidence": "The field is absent."},
            {"check_id": "G1", "met": True, "evidence": "Section 1 contains it."},
        ]
        self.assertEqual(module.validate_gold_submission(expected, valid), {"G1": True, "G2": False})
        with self.assertRaisesRegex(module.ProtocolError, "missing=G2"):
            module.validate_gold_submission(expected, valid[1:])
        with self.assertRaisesRegex(module.ProtocolError, "duplicate"):
            module.validate_gold_submission(expected, [valid[1], valid[1]])
        with self.assertRaisesRegex(module.ProtocolError, "extra=G3"):
            module.validate_gold_submission(expected, valid + [
                {"check_id": "G3", "met": True, "evidence": "Unexpected criterion."}
            ])

    def test_v2_bootstrap_and_agreement_are_deterministic(self):
        module = load_module("protocol_v2_statistics_test", "evals/protocol_v2.py")
        first = module.paired_bootstrap_ci(
            [8, 10, 12, 14], iterations=500, confidence_level=0.95, seed=42
        )
        second = module.paired_bootstrap_ci(
            [8, 10, 12, 14], iterations=500, confidence_level=0.95, seed=42
        )
        self.assertEqual(first, second)
        self.assertGreater(first["lower"], 0)
        agreement = module.agreement_report(
            {name: ([4, 3, 2], [4, 3, 2]) for name in module.DEFAULT_DIMENSION_WEIGHTS},
            ([False, False, True], [False, False, True]),
            ([True, False, True], [True, False, True]),
        )
        self.assertTrue(all(
            item["quadratic_weighted_kappa"] == 1.0
            for item in agreement["dimensions"].values()
        ))
        self.assertEqual(agreement["critical_failure"]["cohen_kappa"], 1.0)
        self.assertEqual(agreement["gold_checks"]["cohen_kappa"], 1.0)

    def test_v2_manifest_refuses_legacy_relabelling(self):
        module = load_module("protocol_v2_legacy_test", "evals/protocol_v2.py")
        config = json.loads((ROOT / "evals/protocol-v2.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(module.LegacyRunError, "legacy evidence"):
            module.assert_v2_manifest(
                {"schema_version": "1.0", "run_id": "legacy"},
                config,
                rubric_sha256="a" * 64,
                rater_schema_sha256="b" * 64,
            )

    def test_v2_executes_rater_schema_and_binds_identity_and_response(self):
        module = load_module("protocol_v2_rater_schema_test", "evals/protocol_v2.py")
        case = {
            "id": "RATE-01",
            "gold_checks": [{"id": "G1", "criterion": "Required field"}],
        }
        response_hashes = {label: label.lower() * 64 for label in module.BLIND_LABELS}
        rubric_sha256 = module.sha256_file(ROOT / "evals/rubric.md")
        document = {
            "schema_version": "2.0",
            "case_id": "RATE-01",
            "rater_id": "rater-one",
            "rater_role": "primary",
            "call_id": "call-rate-01-r1",
            "model": "test-model",
            "rated_at": "2026-08-11T00:02:00+00:00",
            "rubric_sha256": rubric_sha256,
            "ratings": [{
                "blind_label": label,
                "response_sha256": response_hashes[label],
                "scores": {name: 3 for name in module.DEFAULT_DIMENSION_WEIGHTS},
                "critical_failure": False,
                "failure_reason": "",
                "gold_checks": [{"check_id": "G1", "met": True, "evidence": "Section 1"}],
                "rationale": "Traceable synthetic rationale",
            } for label in module.BLIND_LABELS],
        }
        kwargs = {
            "expected_rater_id": "rater-one",
            "expected_role": "primary",
            "rubric_sha256": rubric_sha256,
            "response_hashes": response_hashes,
            "schema_path": ROOT / "evals/rater-output-v2.schema.json",
        }
        self.assertEqual(
            set(module.validate_rater_output(case, document, module.DEFAULT_DIMENSION_WEIGHTS, **kwargs)),
            set(module.BLIND_LABELS),
        )
        missing_model = copy.deepcopy(document)
        del missing_model["model"]
        with self.assertRaisesRegex(module.ProtocolError, "missing required property model"):
            module.validate_rater_output(case, missing_model, module.DEFAULT_DIMENSION_WEIGHTS, **kwargs)
        with self.assertRaisesRegex(module.ProtocolError, "identity mismatch"):
            module.validate_rater_output(
                case,
                document,
                module.DEFAULT_DIMENSION_WEIGHTS,
                **{**kwargs, "expected_rater_id": "different-rater"},
            )
        critical_without_reason = copy.deepcopy(document)
        critical_without_reason["ratings"][0]["critical_failure"] = True
        with self.assertRaisesRegex(module.ProtocolError, "explain its critical failure"):
            module.validate_rater_output(
                case, critical_without_reason, module.DEFAULT_DIMENSION_WEIGHTS, **kwargs
            )
        config = json.loads((ROOT / "evals/protocol-v2.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(module.ProtocolError, "adjudicator must be different"):
            module.aggregate_v2(
                Path("missing"), Path("missing"), Path("missing"), Path("missing"),
                config, ["r1", "r2"], "r1",
            )

    def test_v2_aggregate_gates_gold_ci_agreement_and_protocol(self):
        module = load_module("protocol_v2_aggregate_test", "evals/protocol_v2.py")
        config = json.loads((ROOT / "evals/protocol-v2.json").read_text(encoding="utf-8"))
        config = copy.deepcopy(config)
        config["expected_skills"] = ["skill-one"]
        config["release_gate"]["minimum_cases_per_skill"] = 3
        config["bootstrap"]["iterations"] = 200
        cases_document = {
            "schema_version": "2.0",
            "split": "frozen_holdout",
            "cases": [
                {
                    "id": f"HLD-{index:02d}",
                    "skill": "skill-one",
                    "domain": "test",
                    "prompt": f"Task {index}",
                    "fixture": {},
                    "evaluation_status": "unseen_holdout",
                    "gold_checks": [{"id": "G1", "criterion": "Required field is present"}],
                }
                for index in range(3)
            ],
        }
        secret = "private-aggregate-secret-0000000001"
        nonce = "private-run-nonce-01"
        freeze_id = "aggregate-freeze-001"
        commitment = module.allocation_commitment(
            secret,
            nonce,
            protocol_id=config["protocol_id"],
            freeze_id=freeze_id,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            evals_dir = temp_path / "evals"
            evals_dir.mkdir()
            for filename in (
                "rubric-v2.md",
                "rater-output-v2.schema.json",
                "allocation-reveal-v2.schema.json",
            ):
                (evals_dir / filename).write_bytes((ROOT / "evals" / filename).read_bytes())
            development_path = temp_path / "development.json"
            development_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
            cases_path = temp_path / "holdout.json"
            lock_path = temp_path / "holdout.lock.json"
            registry_path = temp_path / "first-attempt.json"
            config["dataset"].update({
                "development_cases_path": development_path.name,
                "frozen_holdout_cases_path": cases_path.name,
                "frozen_holdout_lock_path": lock_path.name,
                "first_attempt_registry_path": registry_path.name,
                "precommitted_rater_ids": ["r1", "r2"],
                "known_case_sources": [{"kind": "ids", "case_ids": [], "reason": "test"}],
                "pilot_case_ids": [],
            })
            cases_path.write_text(json.dumps(cases_document), encoding="utf-8")
            lock = module.build_holdout_lock(
                cases_path,
                protocol_id=config["protocol_id"],
                freeze_id=freeze_id,
                allocation_commitment_sha256=commitment,
                frozen_at="2026-08-11T00:00:00+00:00",
            )
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            registry = module.build_first_attempt_registry(
                lock,
                run_id="synthetic-v2",
                registered_at="2026-08-11T00:00:30+00:00",
            )
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            arm_map = module.balanced_arm_map(
                cases_document["cases"],
                secret,
                nonce,
                protocol_id=config["protocol_id"],
                freeze_id=freeze_id,
            )
            run_dir = Path(temp_dir) / "run"
            (run_dir / "private").mkdir(parents=True)
            rubric_sha256 = module.sha256_file(evals_dir / "rubric-v2.md")
            rater_schema_sha256 = module.sha256_file(evals_dir / "rater-output-v2.schema.json")
            skill_commit = "d" * 40
            (run_dir / "run-manifest.json").write_text(json.dumps({
                "schema_version": "2.0",
                "protocol_version": "2.0",
                "protocol_id": config["protocol_id"],
                "protocol_config_sha256": module.protocol_config_sha256(config),
                "run_id": "synthetic-v2",
                "created_at": "2026-08-11T00:01:00+00:00",
                "dataset_split": "frozen_holdout",
                "holdout_attempt": 1,
                "holdout_lock_sha256": module.sha256_file(lock_path),
                "holdout_cases_sha256": module.sha256_file(cases_path),
                "first_attempt_registry_sha256": module.sha256_file(registry_path),
                "allocation_commitment_sha256": commitment,
                "skill_commit": skill_commit,
                "rubric_sha256": rubric_sha256,
                "rater_schema_sha256": rater_schema_sha256,
                "case_ids": [case["id"] for case in cases_document["cases"]],
                "case_fingerprints": module.case_fingerprint_map(cases_document),
            }), encoding="utf-8")
            (run_dir / "private/arm-map.json").write_text(
                json.dumps({
                    "method": config["allocation"]["method"],
                    "commitment_sha256": commitment,
                    "arm_map": arm_map,
                }),
                encoding="utf-8",
            )
            for case in cases_document["cases"]:
                case_id = case["id"]
                for label in module.BLIND_LABELS:
                    task_dir = run_dir / "tasks" / case_id / label
                    task_dir.mkdir(parents=True)
                    response_path = task_dir / "response.md"
                    response_path.write_text(f"# Response\n{case_id} {label}\n", encoding="utf-8")
                    events_path = task_dir / "events.jsonl"
                    events_path.write_text("\n".join([
                        json.dumps({
                            "type": "item.completed",
                            "item": {
                                "id": f"{case_id}-{label}-local",
                                "type": "command_execution",
                                "command": 'powershell.exe -Command "Get-Content fixture.json"',
                            },
                        }),
                        json.dumps({
                            "type": "turn.completed",
                            "status": "completed",
                            "turn_id": f"turn-{case_id}-{label}",
                        }),
                    ]) + "\n", encoding="utf-8")
                    (task_dir / "execution.json").write_text(json.dumps({
                        "status": "completed",
                        "returncode": 0,
                        "duration_seconds": 5,
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                        "instruction_read_audit": {"verified": True},
                        "response_sha256": module.sha256_file(response_path),
                        "events_sha256": module.sha256_file(events_path),
                        "skill_commit": skill_commit,
                        "rubric_sha256": rubric_sha256,
                    }), encoding="utf-8")
                for rater in ("r1", "r2"):
                    rating_dir = run_dir / "ratings" / rater
                    rating_dir.mkdir(parents=True, exist_ok=True)
                    ratings = []
                    for label in module.BLIND_LABELS:
                        arm = arm_map[case_id][label]
                        score = 4 if arm == "distilled_skill" else 2
                        ratings.append({
                            "blind_label": label,
                            "response_sha256": module.sha256_file(
                                run_dir / "tasks" / case_id / label / "response.md"
                            ),
                            "scores": {name: score for name in module.DEFAULT_DIMENSION_WEIGHTS},
                            "critical_failure": False,
                            "failure_reason": "",
                            "gold_checks": [{
                                "check_id": "G1",
                                "met": arm == "distilled_skill",
                                "evidence": "Synthetic evidence",
                            }],
                            "rationale": "Synthetic rating",
                        })
                    (rating_dir / f"{case_id}.json").write_text(json.dumps({
                        "schema_version": "2.0",
                        "case_id": case_id,
                        "rater_id": rater,
                        "rater_role": "primary",
                        "call_id": f"call-{rater}-{case_id}",
                        "model": "synthetic-test-model",
                        "rated_at": "2026-08-11T00:02:00+00:00" if rater == "r1" else "2026-08-11T00:03:00+00:00",
                        "rubric_sha256": rubric_sha256,
                        "ratings": ratings,
                    }), encoding="utf-8")
            rating_file_hashes = {
                path.relative_to(run_dir).as_posix(): module.sha256_file(path)
                for rater in ("r1", "r2")
                for path in sorted((run_dir / "ratings" / rater).glob("*.json"))
            }
            ratings_bundle_sha256 = module.canonical_sha256(rating_file_hashes)
            (run_dir / "reveal").mkdir()
            (run_dir / "reveal/allocation-reveal.json").write_text(json.dumps({
                "schema_version": "2.0",
                "protocol_id": config["protocol_id"],
                "freeze_id": freeze_id,
                "secret": secret,
                "nonce": nonce,
                "commitment_sha256": commitment,
                "ratings_bundle_sha256": ratings_bundle_sha256,
                "revealed_at": "2026-08-11T00:04:00+00:00",
            }), encoding="utf-8")
            result = module.aggregate_v2(
                run_dir,
                cases_path,
                lock_path,
                registry_path,
                config,
                ["r1", "r2"],
                base_dir=temp_path,
            )
            self.assertTrue(result["complete"], result)
            self.assertTrue(result["release_gate_passed"], result)
            self.assertEqual(result["protocol_deviation_count"], 0)
            self.assertTrue(result["evidence_integrity"]["valid"], result["evidence_integrity"])
            self.assertGreater(
                result["skill_results"]["skill-one"]["paired_bootstrap_ci"]["lower"],
                0,
            )
            self.assertEqual(
                result["skill_results"]["skill-one"]["arms"]["distilled_skill"]["gold_check_rate"],
                1.0,
            )
            aggregate_path = temp_path / "aggregate.json"
            aggregate_path.write_text(json.dumps(result), encoding="utf-8")
            config["dataset"].update({
                "validated_aggregate_path": aggregate_path.name,
                "validated_aggregate_sha256": module.sha256_file(aggregate_path),
                "validated_run_dir_path": run_dir.name,
            })
            release_status = module.protocol_status(config, temp_path)
            self.assertTrue(release_status["release_gate_passed"], release_status)
            self.assertTrue(release_status["validated_aggregate"]["raw_recomputed"])
            forged_result = copy.deepcopy(result)
            forged_result["rows"] = []
            aggregate_path.write_text(json.dumps(forged_result), encoding="utf-8")
            config["dataset"]["validated_aggregate_sha256"] = module.sha256_file(aggregate_path)
            forged_release_status = module.protocol_status(config, temp_path)
            self.assertFalse(forged_release_status["release_gate_passed"], forged_release_status)
            self.assertIn(
                "validated-aggregate-does-not-match-raw-recomputation",
                forged_release_status["validated_aggregate"]["issues"],
            )
            aggregate_path.write_text(json.dumps(result), encoding="utf-8")
            config["dataset"]["validated_aggregate_sha256"] = module.sha256_file(aggregate_path)
            with self.assertRaisesRegex(module.ProtocolError, "cannot contain path separators"):
                module.aggregate_v2(
                    run_dir,
                    cases_path,
                    lock_path,
                    registry_path,
                    config,
                    ["../../outside-r1", "../../outside-r2"],
                    base_dir=temp_path,
                )
            reveal_path = run_dir / "reveal/allocation-reveal.json"
            original_reveal = json.loads(reveal_path.read_text(encoding="utf-8"))
            reveal_path.write_text(
                json.dumps({**original_reveal, "revealed_at": "2026-08-11T00:03:00+00:00"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(module.ProtocolError, "strictly after"):
                module.aggregate_v2(
                    run_dir, cases_path, lock_path, registry_path, config, ["r1", "r2"],
                    base_dir=temp_path,
                )
            reveal_path.write_text(json.dumps(original_reveal), encoding="utf-8")
            probe_response = run_dir / "tasks/HLD-00/A/response.md"
            original_response = probe_response.read_text(encoding="utf-8")
            probe_response.write_text("\x00", encoding="utf-8")
            nul_deviations, _ = module._execution_audit(
                run_dir,
                "HLD-00",
                "A",
                config["tool_policy"],
                skill_commit=skill_commit,
                rubric_sha256=rubric_sha256,
            )
            self.assertIn("response-empty", {item["code"] for item in nul_deviations})
            probe_response.write_text("\u0301\ufe0f", encoding="utf-8")
            mark_only_deviations, _ = module._execution_audit(
                run_dir,
                "HLD-00",
                "A",
                config["tool_policy"],
                skill_commit=skill_commit,
                rubric_sha256=rubric_sha256,
            )
            self.assertIn("response-empty", {item["code"] for item in mark_only_deviations})
            probe_response.write_text(original_response, encoding="utf-8")
            with self.assertRaisesRegex(TypeError, "dataset_audit"):
                module.aggregate_v2(
                    run_dir, cases_path, lock_path, registry_path, config, ["r1", "r2"],
                    dataset_audit={"eligible": True},
                )
            rating_path = run_dir / "ratings/r1/HLD-00.json"
            original_rating = rating_path.read_text(encoding="utf-8")
            rating_path.write_text(original_rating + "\n", encoding="utf-8")
            with self.assertRaisesRegex(module.ProtocolError, "rating bundle"):
                module.aggregate_v2(
                    run_dir, cases_path, lock_path, registry_path, config, ["r1", "r2"],
                    base_dir=temp_path,
                )
            rating_path.write_text(original_rating, encoding="utf-8")
            contaminated_case = cases_document["cases"][0]["id"]
            contaminated_label = next(iter(module.BLIND_LABELS))
            execution_path = run_dir / "tasks" / contaminated_case / contaminated_label / "execution.json"
            original_execution = json.loads(execution_path.read_text(encoding="utf-8"))
            bad_binding = {**original_execution, "skill_commit": "e" * 40, "rubric_sha256": "f" * 64}
            execution_path.write_text(json.dumps(bad_binding), encoding="utf-8")
            binding_failure = module.aggregate_v2(
                run_dir, cases_path, lock_path, registry_path, config, ["r1", "r2"],
                base_dir=temp_path,
            )
            self.assertFalse(binding_failure["release_gate_passed"])
            binding_codes = {item["code"] for item in binding_failure["protocol_deviations"]}
            self.assertIn("execution-skill-commit-mismatch", binding_codes)
            self.assertIn("execution-rubric-sha256-mismatch", binding_codes)
            execution_path.write_text(json.dumps(original_execution), encoding="utf-8")
            (run_dir / "tasks" / contaminated_case / contaminated_label / "response.md").write_text(
                "tampered after rating\n", encoding="utf-8"
            )
            contaminated = module.aggregate_v2(
                run_dir,
                cases_path,
                lock_path,
                registry_path,
                config,
                ["r1", "r2"],
                base_dir=temp_path,
            )
            self.assertFalse(contaminated["release_gate_passed"])
            self.assertFalse(contaminated["evidence_integrity"]["valid"])
            self.assertIn(
                "execution-response-sha256-mismatch",
                {item["code"] for item in contaminated["protocol_deviations"]},
            )


if __name__ == "__main__":
    unittest.main()
