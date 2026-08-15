from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "check_distribution_release.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_distribution_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DistributionReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = _load_checker()
        cls.report = cls.checker.evaluate(ROOT)

    def test_release_gate_passes_and_reports_every_check(self):
        self.assertTrue(self.report["valid"], self.report["failed_checks"])
        self.assertGreaterEqual(len(self.report["checks"]), 9)
        self.assertTrue(all(item["passed"] for item in self.report["checks"]))

    def test_distribution_stable_does_not_promote_capability_evidence(self):
        release = json.loads(
            (ROOT / "provenance" / "distribution-release-v1.json").read_text(encoding="utf-8")
        )
        evaluation = yaml.safe_load(
            (ROOT / "provenance" / "evaluation-status-v3.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(release["distribution_status"], "stable")
        self.assertEqual(release["capability_evaluation_status"], "beta")
        self.assertIsNone(release["official_portfolio_score"])
        self.assertEqual(evaluation["current_status"], "beta")
        self.assertIsNone(evaluation["official_portfolio_score"])

    def test_all_twenty_skills_are_in_the_release_inventory(self):
        matrix = json.loads(
            (ROOT / "evals" / "skill-evaluation-matrix-v3.json").read_text(encoding="utf-8")
        )
        directories = sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir())
        matrix_ids = sorted(item["skill_id"] for item in matrix["skills"])
        self.assertEqual(len(directories), 20)
        self.assertEqual(directories, matrix_ids)

    def test_cross_agent_manifests_use_the_same_release(self):
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        opencode = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["version"], "1.0.0")
        self.assertEqual(claude["version"], "1.0.0")
        self.assertEqual(codex["name"], claude["name"])
        self.assertEqual(opencode["skills"], {"paths": ["./skills"]})

    def test_release_gate_cli_is_cwd_independent(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(ROOT)],
            cwd=ROOT.parent,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertTrue(json.loads(completed.stdout)["valid"])

    def test_git_archive_contains_the_release_and_excludes_local_state(self):
        completed = subprocess.run(
            ["git", "archive", "--format=zip", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        with zipfile.ZipFile(io.BytesIO(completed.stdout)) as archive:
            names = set(archive.namelist())
        required = {
            ".codex-plugin/plugin.json",
            ".claude-plugin/plugin.json",
            "opencode.json",
            "LICENSE",
            "NOTICE",
            "provenance/distribution-release-v1.json",
        }
        self.assertTrue(required <= names, required - names)
        archived_skills = {
            name.split("/")[1]
            for name in names
            if name.startswith("skills/") and name.endswith("/SKILL.md") and name.count("/") == 2
        }
        self.assertEqual(len(archived_skills), 20)
        forbidden = [
            name
            for name in names
            if "/__pycache__/" in f"/{name}"
            or name.endswith((".pyc", ".pyo"))
            or name.startswith((".git/", "evals/cache/"))
        ]
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
