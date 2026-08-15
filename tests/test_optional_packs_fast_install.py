from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "tools" / "zju_skills.py"
FAST = ROOT / "skills" / "zju-research-director" / "scripts" / "route_fast_request.py"
REGISTRY = ROOT / "skills" / "zju-research-director" / "references" / "capability-registry.yaml"
SECURITY_SCAN = ROOT / "tests" / "security_scan.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OptionalPackExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.omics = load_module(
            ROOT / "packs/omics/skills/zju-omics-analysis/scripts/omics_screen.py", "omics_screen"
        )
        cls.materials = load_module(
            ROOT / "packs/materials/skills/zju-materials-computation/scripts/analyze_structure.py",
            "analyze_structure",
        )
        cls.drug = load_module(
            ROOT / "packs/drug-discovery/skills/zju-drug-discovery/scripts/prioritize_candidates.py",
            "prioritize_candidates",
        )

    def test_omics_executor_aligns_samples_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix = root / "counts.csv"
            matrix.write_text(
                "feature_id,A2,A1,B2,B1\n"
                "gene-up,8,10,90,100\n"
                "gene-flat,30,32,31,33\n"
                "gene-low,0,0,1,0\n",
                encoding="utf-8",
            )
            samples = root / "samples.csv"
            samples.write_text(
                "sample_id,group,batch\nA1,control,x\nA2,control,y\nB1,treated,x\nB2,treated,y\n",
                encoding="utf-8",
            )
            first = self.omics.execute(matrix, samples, min_total=10, min_samples=2)
            second = self.omics.execute(matrix, samples, min_total=10, min_samples=2)
            self.assertEqual(first, second)
            self.assertEqual(first["design"]["sample_ids"], ["A1", "A2", "B1", "B2"])
            self.assertEqual(first["analysis_class"], "exploratory_screen")
            self.assertEqual(first["qc"]["retained_features"], 2)
            top = first["differential_screen"]["results"][0]
            self.assertEqual(top["feature_id"], "gene-up")
            self.assertGreater(top["log2_cpm_difference"], 0)

    def test_omics_executor_rejects_sample_mismatch_and_repeated_design_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix = root / "counts.csv"
            samples = root / "samples.csv"
            matrix.write_text("feature_id,A,B\ng,1,2\n", encoding="utf-8")
            samples.write_text("sample_id,group\nA,x\nC,y\n", encoding="utf-8")
            with self.assertRaisesRegex(self.omics.OmicsInputError, "sample mismatch"):
                self.omics.execute(matrix, samples, min_total=1, min_samples=1)

            matrix.write_text(
                "feature_id,A1,A2,B1,B2\ng1,10,12,30,32\ng2,20,22,25,27\n",
                encoding="utf-8",
            )
            samples.write_text(
                "sample_id,group,subject_id\n"
                "A1,control,S1\nA2,control,S2\nB1,treated,S1\nB2,treated,S2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(self.omics.OmicsInputError, "repeated subject_id"):
                self.omics.execute(matrix, samples, min_total=1, min_samples=1)

    def test_omics_executor_rejects_group_batch_confounding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix = root / "counts.csv"
            samples = root / "samples.csv"
            matrix.write_text(
                "feature_id,A1,A2,B1,B2\ng1,10,12,30,32\ng2,20,22,25,27\n",
                encoding="utf-8",
            )
            samples.write_text(
                "sample_id,group,batch\n"
                "A1,control,run-1\nA2,control,run-1\nB1,treated,run-2\nB2,treated,run-2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(self.omics.OmicsInputError, "confounded"):
                self.omics.execute(matrix, samples, min_total=1, min_samples=1)

    def test_materials_executor_computes_cell_density_and_periodic_distance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "si.json"
            path.write_text(
                json.dumps(
                    {
                        "structure_id": "Si-test",
                        "lattice_angstrom": [[5.43, 0, 0], [0, 5.43, 0], [0, 0, 5.43]],
                        "sites": [
                            {"element": "Si", "fractional": [0, 0, 0]},
                            {"element": "Si", "fractional": [0.25, 0.25, 0.25]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.materials.analyze(path)
            self.assertEqual(result["structure_id"], "Si-test")
            self.assertAlmostEqual(result["cell"]["volume_angstrom3"], 5.43**3, places=7)
            self.assertEqual(result["composition"]["formula"], "Si2")
            self.assertGreater(result["density_g_cm3"], 0)
            self.assertGreater(result["periodic_distance_check"]["minimum_distance_angstrom"], 2)
            self.assertEqual(result["scientific_claim_status"], "geometry_check_only")

    def test_materials_executor_rejects_singular_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                json.dumps({"lattice_angstrom": [[1, 0, 0], [2, 0, 0], [0, 0, 1]], "sites": [{"element": "C", "fractional": [0, 0, 0]}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(self.materials.StructureInputError, "singular"):
                self.materials.analyze(path)

    def test_materials_executor_handles_unwrapped_coordinates_and_cooccupancy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unwrapped = root / "unwrapped.json"
            unwrapped.write_text(
                json.dumps({
                    "lattice_angstrom": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                    "sites": [
                        {"element": "C", "fractional": [0, 0, 0]},
                        {"element": "C", "fractional": [2.1, 0, 0]},
                    ],
                }),
                encoding="utf-8",
            )
            result = self.materials.analyze(unwrapped)
            self.assertAlmostEqual(result["periodic_distance_check"]["minimum_distance_angstrom"], 0.1)

            disordered = root / "disordered.json"
            disordered.write_text(
                json.dumps({
                    "lattice_angstrom": [[4, 0, 0], [1.5, 3.7, 0], [0.2, 0.4, 5]],
                    "sites": [
                        {"element": "Na", "fractional": [0, 0, 0], "occupancy": 0.5},
                        {"element": "K", "fractional": [1, 0, 0], "occupancy": 0.5},
                        {"element": "Cl", "fractional": [0.5, 0.5, 0.5]},
                    ],
                }),
                encoding="utf-8",
            )
            result = self.materials.analyze(disordered)
            self.assertEqual(result["composition"]["geometric_sites"], 2)
            self.assertGreater(result["periodic_distance_check"]["minimum_distance_angstrom"], 1)
            self.assertTrue(any("co-occupied" in warning for warning in result["warnings"]))

    def test_drug_executor_preserves_sources_exclusions_and_sensitivity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "candidate_id": "C-good",
                                "target_id": "T1",
                                "compound_id": "CHEMBL1",
                                "potency_nM": 5,
                                "selectivity_ratio": 100,
                                "evidence": [
                                    {"lane": lane, "value": 0.8, "source_id": f"SRC-{lane}"}
                                    for lane in self.drug.LANE_WEIGHTS
                                ],
                            },
                            {
                                "candidate_id": "C-excluded",
                                "target_id": "T2",
                                "compound_id": "CHEMBL2",
                                "potency_nM": 1,
                                "selectivity_ratio": 1000,
                                "hard_exclusion": True,
                                "safety_flags": ["genotoxicity-signal"],
                                "evidence": [
                                    {"lane": lane, "value": 1.0, "source_id": f"SRC2-{lane}"}
                                    for lane in self.drug.LANE_WEIGHTS
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = self.drug.prioritize(path)
            self.assertEqual(result["ranking"][0]["candidate_id"], "C-good")
            excluded = result["ranking"][1]
            self.assertEqual(excluded["score"], 0)
            self.assertTrue(excluded["hard_exclusion"])
            self.assertIn("genotoxicity-signal", excluded["safety_flags"])
            self.assertEqual(len(result["sensitivity_scenarios"]), 8)

    def test_drug_executor_rejects_null_sources_and_string_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            base = {
                "candidate_id": "C1",
                "target_id": "T1",
                "compound_id": "CHEMBL1",
                "evidence": [{"lane": "genetic", "value": 0.5, "source_id": None}],
            }
            path.write_text(json.dumps({"candidates": [base]}), encoding="utf-8")
            with self.assertRaisesRegex(self.drug.CandidateInputError, "source_id"):
                self.drug.prioritize(path)
            base["evidence"][0]["source_id"] = "SRC-1"
            base["hard_exclusion"] = "false"
            path.write_text(json.dumps({"candidates": [base]}), encoding="utf-8")
            with self.assertRaisesRegex(self.drug.CandidateInputError, "JSON boolean"):
                self.drug.prioritize(path)

    def test_every_optional_pack_executor_is_in_the_static_scan_scope(self):
        scanner = load_module(SECURITY_SCAN, "security_scan_for_optional_packs")
        pack_scripts = sorted(ROOT.glob("packs/*/skills/*/scripts/*.py"))
        self.assertEqual(len(pack_scripts), 3)
        self.assertEqual(
            {path.name for path in pack_scripts},
            {"omics_screen.py", "analyze_structure.py", "prioritize_candidates.py"},
        )
        findings = [finding for path in pack_scripts for finding in scanner.scan_script(path)]
        self.assertEqual(findings, [])


class FastModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fast = load_module(FAST, "route_fast_request")
        cls.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_routes_one_unambiguous_output_without_claiming_execution(self):
        result = self.fast.route(
            {
                "request_id": "FAST-001",
                "objective": "Normalize a supplied literature export",
                "requested_output": "literature_set",
                "available_inputs": ["metadata_export"],
                "constraints": ["offline"],
            },
            self.registry,
        )
        self.assertEqual(result["selected_specialist"], "zju-literature-search")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["execution_claim"], "not_executed")
        self.assertEqual(result["input_packet"]["recognized_inputs"], ["metadata_export"])

    def test_high_risk_or_unknown_output_escalates_or_rejects(self):
        result = self.fast.route(
            {
                "request_id": "FAST-002",
                "objective": "Prepare a filing aid",
                "requested_output": "technical_disclosure",
                "available_inputs": ["paper"],
            },
            self.registry,
        )
        self.assertEqual(result["status"], "escalate")
        with self.assertRaisesRegex(self.fast.FastRouteError, "no specialist"):
            self.fast.route(
                {"request_id": "FAST-003", "objective": "x", "requested_output": "unknown", "available_inputs": []},
                self.registry,
            )

    def test_optional_pack_is_routable_only_when_declared_available(self):
        request = {
            "request_id": "FAST-PACK-001",
            "objective": "Validate a count matrix",
            "requested_output": "omics_screen",
            "available_inputs": ["omics_count_matrix", "sample_metadata"],
        }
        with self.assertRaisesRegex(self.fast.FastRouteError, "no specialist"):
            self.fast.route(request, self.registry)
        request["available_optional_skills"] = ["zju-omics-analysis"]
        result = self.fast.route(
            request,
            self.registry,
            installed_optional_skills=["zju-omics-analysis"],
        )
        self.assertEqual(result["selected_specialist"], "zju-omics-analysis")
        self.assertEqual(result["status"], "ready")

    def test_fast_mode_needs_recognized_inputs_and_rejects_spoofed_pack_availability(self):
        result = self.fast.route(
            {
                "request_id": "FAST-NEEDS-INPUT",
                "objective": "Normalize a literature export",
                "requested_output": "literature_set",
                "available_inputs": [],
            },
            self.registry,
        )
        self.assertEqual(result["status"], "needs_input")
        self.assertTrue(result["input_packet"]["missing_required_input_groups"])

        spoofed = {
            "request_id": "FAST-SPOOFED-PACK",
            "objective": "Screen an omics matrix",
            "requested_output": "omics_screen",
            "available_inputs": ["omics_count_matrix", "sample_metadata"],
            "available_optional_skills": ["zju-omics-analysis"],
        }
        with self.assertRaisesRegex(self.fast.FastRouteError, "no specialist"):
            self.fast.route(spoofed, self.registry)

    def test_optional_pack_required_input_groups_are_enforced(self):
        request = {
            "request_id": "FAST-PACK-MISSING-SAMPLES",
            "objective": "Screen an omics matrix",
            "requested_output": "omics_screen",
            "available_inputs": ["omics_count_matrix"],
            "available_optional_skills": ["zju-omics-analysis"],
        }
        result = self.fast.route(
            request,
            self.registry,
            installed_optional_skills=["zju-omics-analysis"],
        )
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["input_packet"]["missing_required_input_groups"], [["sample_metadata"]])


class InstallManagerTests(unittest.TestCase):
    def run_manager(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "PYTHONUTF8": "1"}
        return subprocess.run(
            [sys.executable, str(MANAGER), *arguments],
            cwd=ROOT.parent,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )

    def test_dry_run_has_no_filesystem_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "new-home"
            completed = self.run_manager("install", "--agent", "all", "--home", str(home), "--dry-run")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(home.exists())

    def test_install_all_agents_with_optional_packs_and_doctor(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            arguments = [
                "install", "--agent", "all", "--home", str(home),
                "--pack", "omics", "--pack", "materials", "--pack", "drug-discovery",
            ]
            completed = self.run_manager(*arguments)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(len(report["reports"]), 3)
            for agent_report in report["reports"]:
                target = Path(agent_report["target"])
                self.assertEqual(len(list(target.glob("*/SKILL.md"))), 23)
                state = json.loads((target / ".zju-research-skills-install.json").read_text(encoding="utf-8"))
                self.assertEqual(len(state["installed_skills"]), 23)
            doctor = self.run_manager("doctor", "--agent", "all", "--home", str(home))
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            self.assertTrue(json.loads(doctor.stdout)["valid"])

    def test_installer_refuses_unmanaged_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            collision = home / ".codex" / "skills" / "zju-paper-reader"
            collision.mkdir(parents=True)
            (collision / "SKILL.md").write_text("user-owned", encoding="utf-8")
            completed = self.run_manager("install", "--agent", "codex", "--home", str(home))
            self.assertEqual(completed.returncode, 2)
            self.assertIn("unmanaged", completed.stderr)
            self.assertEqual((collision / "SKILL.md").read_text(encoding="utf-8"), "user-owned")

    def test_update_is_idempotent_and_reports_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = self.run_manager("install", "--agent", "codex", "--home", str(home), "--pack", "omics")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            updated = self.run_manager("update", "--agent", "codex", "--home", str(home), "--pack", "omics")
            self.assertEqual(updated.returncode, 0, updated.stderr)
            actions = json.loads(updated.stdout)["reports"][0]["actions"]
            self.assertTrue(actions)
            self.assertTrue(all(item["action"] == "unchanged" for item in actions))

    def test_update_without_pack_arguments_preserves_installed_packs(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = self.run_manager(
                "install", "--agent", "codex", "--home", str(home), "--pack", "omics"
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            updated = self.run_manager("update", "--agent", "codex", "--home", str(home))
            self.assertEqual(updated.returncode, 0, updated.stderr)
            target = home / ".codex" / "skills"
            state = json.loads((target / ".zju-research-skills-install.json").read_text(encoding="utf-8"))
            self.assertEqual(state["packs"], ["omics"])
            self.assertIn("zju-omics-analysis", state["installed_skills"])
            self.assertTrue((target / "zju-omics-analysis" / "SKILL.md").is_file())

    def test_late_unmanaged_collision_is_preflighted_before_any_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / ".codex" / "skills"
            collision = target / "zju-statistics-audit"
            collision.mkdir(parents=True)
            (collision / "SKILL.md").write_text("user-owned", encoding="utf-8")
            completed = self.run_manager("install", "--agent", "codex", "--home", str(home))
            self.assertEqual(completed.returncode, 2)
            self.assertIn("unmanaged", completed.stderr)
            self.assertEqual(
                sorted(path.name for path in target.iterdir()),
                ["zju-statistics-audit"],
            )
            self.assertEqual((collision / "SKILL.md").read_text(encoding="utf-8"), "user-owned")

    def test_identical_unmanaged_skill_is_not_silently_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / ".codex" / "skills"
            target.mkdir(parents=True)
            source = ROOT / "skills" / "zju-paper-reader"
            destination = target / "zju-paper-reader"
            import shutil

            shutil.copytree(source, destination)
            completed = self.run_manager("install", "--agent", "codex", "--home", str(home))
            self.assertEqual(completed.returncode, 2)
            self.assertIn("unmanaged", completed.stderr)
            self.assertFalse((target / ".zju-research-skills-install.json").exists())

    def test_all_agent_collisions_are_preflighted_before_first_target_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            collision = home / ".claude" / "skills" / "zju-reviewer"
            collision.mkdir(parents=True)
            (collision / "SKILL.md").write_text("user-owned", encoding="utf-8")
            completed = self.run_manager("install", "--agent", "all", "--home", str(home))
            self.assertEqual(completed.returncode, 2)
            self.assertIn("unmanaged", completed.stderr)
            self.assertFalse((home / ".codex" / "skills").exists())
            self.assertFalse((home / ".config" / "opencode" / "skills").exists())

    def test_dry_run_with_pull_never_mutates_or_requires_a_clean_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            completed = self.run_manager(
                "update",
                "--agent", "codex",
                "--home", str(home),
                "--source", str(ROOT),
                "--dry-run",
                "--pull",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "dry_run")
            self.assertFalse(home.exists())

    def test_runtime_install_can_be_planned_without_running_pip(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            completed = self.run_manager(
                "install", "--agent", "codex", "--home", str(home), "--with-runtime", "--dry-run"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["runtime"]["status"], "planned")
            self.assertIn("requirements-runtime.txt", report["runtime"]["requirements"])
            self.assertFalse(home.exists())

    def test_doctor_reports_a_missing_executor_dependency(self):
        manager = load_module(MANAGER, "zju_skills_runtime_doctor_test")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / ".codex" / "skills"
            manager._install_target(
                root=ROOT, target=target, agent="codex", packs=[], dry_run=False
            )
            real_find_spec = manager.importlib.util.find_spec

            def dependency_probe(name: str):
                return None if name == "httpx" else real_find_spec(name)

            args = types.SimpleNamespace(
                agent=["codex"], scope="user", project_dir=None, home=str(home)
            )
            with mock.patch.object(manager.importlib.util, "find_spec", dependency_probe):
                report = manager._run_doctor(args)
            self.assertFalse(report["valid"])
            self.assertIn("httpx", report["reports"][0]["problems"][0])

    def test_failed_staged_swap_restores_previous_managed_skill(self):
        manager = load_module(MANAGER, "zju_skills_transaction_test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            skill = source / "skills" / "zju-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("old-source", encoding="utf-8")
            manifest = source / ".codex-plugin" / "plugin.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps({"name": "zju-research-skills", "version": "test"}),
                encoding="utf-8",
            )
            target = root / "target"
            manager._install_target(
                root=source, target=target, agent="codex", packs=[], dry_run=False
            )
            installed = target / "zju-test" / "SKILL.md"
            self.assertEqual(installed.read_text(encoding="utf-8"), "old-source")
            (skill / "SKILL.md").write_text("new-source", encoding="utf-8")

            original_replace = Path.replace

            def fail_between_backup_and_install(path: Path, destination: Path):
                if ".zju-install-" in str(path) and path.name == "zju-test":
                    raise OSError("injected staged rename failure")
                return original_replace(path, destination)

            with mock.patch.object(Path, "replace", fail_between_backup_and_install):
                with self.assertRaisesRegex(manager.InstallError, "rolled back"):
                    manager._install_target(
                        root=source, target=target, agent="codex", packs=[], dry_run=False
                    )
            self.assertEqual(installed.read_text(encoding="utf-8"), "old-source")


if __name__ == "__main__":
    unittest.main()
