from __future__ import annotations

import importlib.util
import json
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
        result = module.verify(module.DEFAULT_CACHE, ROOT / "evals/upstream-lock.json")
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["files"], 306)

    def test_plan_keeps_arm_evidence_private(self):
        module = load_module("blind_eval_plan_test", "evals/blind_eval.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            module.RESULTS = Path(temp_dir) / "results"
            with mock.patch.object(module, "codex_version", return_value="codex-cli test"):
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


if __name__ == "__main__":
    unittest.main()
\n