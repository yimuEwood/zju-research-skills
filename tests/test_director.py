from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECTOR = ROOT / "skills" / "zju-research-director"
SCRIPTS = DIRECTOR / "scripts"
REGISTRY = DIRECTOR / "references" / "capability-registry.yaml"


def utf8_subprocess_env():
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def minimal_mission(**overrides):
    mission = {
        "schema_version": "1.0",
        "mission_id": "MISSION-CONTRACT-001",
        "research_question": "Which intervention most plausibly improves the measured outcome?",
        "domain": "materials",
        "objectives": [
            {
                "objective_id": "O01",
                "statement": "Build an evidence-grounded, falsifiable research plan",
            }
        ],
        "constraints": {"autonomy_ceiling": "L1"},
        "requested_deliverables": ["hypothesis_set"],
        "current_stage": "framing",
        "status": "draft",
        "evidence_records": [],
        "claims": [],
        "hypotheses": [],
        "experiments": [],
        "artifacts": [],
        "decisions": [],
        "risks": [],
        "open_loops": [],
        "route": [],
    }
    for key, value in overrides.items():
        mission[key] = value
    return mission


def release_candidate():
    mission = minimal_mission(
        constraints={"autonomy_ceiling": "L2", "release_intent": True},
        requested_deliverables=["submission_package"],
        current_stage="review",
        status="active",
    )
    mission["evidence_records"] = [
        {
            "evidence_id": "E01",
            "title": "A traceable source",
            "doi": "10.1000/contract-test",
            "retrieval_date": "2026-08-11",
            "source_anchor": "Results, p. 4",
            "version_status": "active",
        }
    ]
    mission["claims"] = [
        {
            "claim_id": "C01",
            "statement": "The tested intervention changed the measured outcome.",
            "status": "supported",
            "evidence_ids": ["E01"],
            "source_anchor": "Results, p. 4",
        }
    ]
    mission["artifacts"] = [
        {
            "artifact_id": "A01",
            "artifact_type": "manuscript",
            "status": "validated",
            "path": "artifacts/manuscript.md",
            "provenance": {"mission_id": mission["mission_id"], "producer": "contract-test"},
        }
    ]
    return mission


class DirectorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planner = load_module("director_plan_mission_contract", SCRIPTS / "plan_mission.py")
        cls.validator = load_module("director_validate_mission_contract", SCRIPTS / "validate_mission.py")
        cls.merger = load_module("director_merge_artifacts_contract", SCRIPTS / "merge_artifacts.py")
        cls.gates = load_module("director_check_stage_gate_contract", SCRIPTS / "check_stage_gate.py")
        cls.state = load_module("director_advance_mission_contract", SCRIPTS / "advance_mission.py")

    def test_plan_is_deterministic_and_materializes_dependency_dag(self):
        source = minimal_mission()
        first = self.planner.plan_mission(copy.deepcopy(source))
        second = self.planner.plan_mission(copy.deepcopy(source))

        self.assertEqual(first["route"], second["route"])
        self.assertEqual(first["dag"], second["dag"])
        self.assertEqual(first["plan_hash"], second["plan_hash"])

        route = first["route"]
        skills = [step["skill"] for step in route]
        self.assertIn("zju-literature-search", skills)
        self.assertIn("zju-evidence-synthesis", skills)
        self.assertIn("zju-hypothesis-design", skills)
        self.assertLess(skills.index("zju-literature-search"), skills.index("zju-evidence-synthesis"))
        self.assertLess(skills.index("zju-evidence-synthesis"), skills.index("zju-hypothesis-design"))

        step_ids = {step["step_id"] for step in route}
        index_by_id = {step["step_id"]: index for index, step in enumerate(route)}
        self.assertEqual(first["dag"]["nodes"], [step["step_id"] for step in route])
        self.assertTrue(first["dag"]["edges"], "A multi-step route must expose dependency edges")
        for step in route:
            self.assertIn("prerequisites", step)
            self.assertIsInstance(step["prerequisites"], list)
            for dependency in step["prerequisites"]:
                self.assertIn(dependency, step_ids)
                self.assertLess(index_by_id[dependency], index_by_id[step["step_id"]])
                self.assertIn(
                    {"from": dependency, "to": step["step_id"]},
                    first["dag"]["edges"],
                )

    def test_plan_never_exceeds_autonomy_ceiling(self):
        ranks = {f"L{level}": level for level in range(5)}
        planned = self.planner.plan_mission(minimal_mission())
        ceiling = planned["constraints"]["autonomy_ceiling"]
        self.assertEqual(ceiling, "L1")
        self.assertTrue(planned["route"])
        for step in planned["route"]:
            self.assertIn(step["autonomy_level"], ranks)
            self.assertLessEqual(ranks[step["autonomy_level"]], ranks[ceiling])

    def test_replanning_an_existing_plan_is_idempotent(self):
        source = minimal_mission(
            requested_deliverables=["custom-lab-decision-packet"],
            required_skills=["zju-literature-search", "zju-evidence-synthesis"],
        )
        first = self.planner.plan_mission(source)
        second = self.planner.plan_mission(first)
        third = self.planner.plan_mission(second)
        self.assertEqual(second["route"], third["route"])
        self.assertEqual(second["dag"], third["dag"])
        self.assertEqual(second["plan_hash"], third["plan_hash"])
        self.assertEqual(second["custom_deliverables"], ["custom_lab_decision_packet"])

    def test_plan_cli_writes_a_valid_deterministic_plan(self):
        script = SCRIPTS / "plan_mission.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "mission.json"
            output_path = Path(temp_dir) / "planned.json"
            input_path.write_text(json.dumps(minimal_mission(), ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--input", str(input_path), "--output", str(output_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=utf8_subprocess_env(),
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(output_path.is_file())
            planned = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertTrue(planned["planning_validation"]["valid"], planned["planning_validation"])
            self.assertEqual(planned["execution_order"], planned["dag"]["nodes"])

    def test_sensitive_actions_always_stop_at_human_gates(self):
        cases = {
            "external_action": release_candidate(),
            "patent_legal": release_candidate(),
            "ethics": release_candidate(),
            "submission_release": release_candidate(),
        }
        cases["ethics"]["constraints"]["ethics_required"] = True

        for gate, mission in cases.items():
            with self.subTest(gate=gate):
                result = self.gates.check_gate(mission, gate)
                self.assertTrue(result["valid"], result)
                self.assertEqual(result["status"], "requires_human_review", result)
                self.assertFalse(result["passed"])
                self.assertTrue(result.get("human_authority"), result)
                self.assertTrue(result.get("required_actions"), result)

    def test_missing_evidence_blocks_claim_and_release(self):
        mission = release_candidate()
        mission["evidence_records"] = []
        for gate in ("claim", "submission_release"):
            with self.subTest(gate=gate):
                result = self.gates.check_gate(mission, gate)
                self.assertEqual(result["status"], "blocked", result)
                self.assertFalse(result["passed"])
                self.assertTrue(result["blockers"])
                self.assertIn("evidence", " ".join(result["blockers"]).lower())

    def test_artifact_collision_is_transactional_and_never_overwrites(self):
        mission = release_candidate()
        original = copy.deepcopy(mission["artifacts"][0])
        collision = {**original, "path": "artifacts/silently-replaced.md"}
        result = self.merger.merge(mission, [collision])

        self.assertFalse(result["valid"], result)
        self.assertEqual(result["mission"]["artifacts"], [original])
        self.assertEqual(result["merged_ids"], [])
        self.assertTrue(result["conflicts"], result)
        self.assertEqual(result["conflicts"][0]["code"], "id_collision")
        self.assertEqual(result["conflicts"][0]["artifact_id"], original["artifact_id"])

    def test_artifact_merge_preserves_open_loops(self):
        mission = release_candidate()
        loop = {
            "loop_id": "LOOP-001",
            "type": "evidence_gap",
            "issue": "External validity remains unresolved",
            "owner": "research_owner",
            "status": "open",
            "blocking": False,
        }
        mission["open_loops"] = [copy.deepcopy(loop)]
        incoming = {
            "artifact_id": "A02",
            "artifact_type": "reviewer_report",
            "status": "created",
            "path": "artifacts/reviewer-report.md",
            "provenance": {"mission_id": mission["mission_id"], "producer": "zju-reviewer"},
        }
        result = self.merger.merge(mission, [incoming])

        self.assertTrue(result["valid"], result)
        self.assertEqual(result["mission"]["open_loops"], [loop])
        self.assertEqual(result["open_loops_preserved"], [loop])
        self.assertEqual(result["merged_ids"], ["A02"])

    def test_state_transition_requires_validated_outputs_and_passed_gates(self):
        planned = self.planner.plan_mission(minimal_mission())
        first = planned["route"][0]
        rejected = self.state.advance(planned, first["step_id"], "completed")
        self.assertFalse(rejected["valid"], rejected)
        codes = {item["code"] for item in rejected["errors"]}
        self.assertIn("missing_output", codes)
        self.assertIn("missing_gate_result", codes)

        artifact = {
            "artifact_id": "A-SEARCH-01",
            "artifact_type": first["expected_outputs"][0],
            "status": "validated",
            "path": "artifacts/search.json",
            "provenance": {"mission_id": planned["mission_id"], "producer": first["skill"]},
        }
        planned["artifacts"].append(artifact)
        gates = [
            {
                "valid": True,
                "gate": gate,
                "status": "passed",
                "passed": True,
                "checked_inputs": [],
                "blockers": [],
                "warnings": [],
                "required_actions": [],
            }
            for gate in first["required_gates"]
        ]
        accepted = self.state.advance(planned, first["step_id"], "completed", [artifact["artifact_id"]], gates)
        self.assertTrue(accepted["valid"], accepted)
        self.assertEqual(accepted["transition"]["to"], "completed")
        self.assertEqual(accepted["mission"]["revision"], planned["revision"] + 1)
        self.assertEqual(accepted["mission"]["route"][0]["produced_artifact_ids"], [artifact["artifact_id"]])
        self.assertTrue(accepted["mission"]["gate_ledger"])

    def test_state_transition_recomputes_gates_instead_of_trusting_a_supplied_pass(self):
        mission = minimal_mission(status="planned")
        mission["route"] = [
            {
                "step_id": "S01",
                "stage": "reading",
                "skill": "zju-paper-reader",
                "prerequisites": [],
                "required_gates": ["evidence"],
                "autonomy_level": "L1",
                "status": "ready",
                "expected_outputs": ["paper_card"],
                "produced_artifact_ids": [],
            }
        ]
        mission["artifacts"] = [
            {
                "artifact_id": "A-CARD-01",
                "artifact_type": "paper_card",
                "status": "validated",
                "path": "artifacts/card.md",
                "provenance": {"mission_id": mission["mission_id"], "producer": "zju-paper-reader"},
            }
        ]
        forged = [{"gate": "evidence", "status": "passed", "passed": True, "blockers": [], "warnings": []}]
        result = self.state.advance(mission, "S01", "completed", ["A-CARD-01"], forged)
        self.assertFalse(result["valid"], result)
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("gate_result_mismatch", codes)
        self.assertIn("gate_not_passed", codes)

    def test_blocked_state_transition_preserves_history_and_adds_open_loop(self):
        planned = self.planner.plan_mission(minimal_mission())
        first = planned["route"][0]
        existing = {
            "loop_id": "LOOP-EXISTING-001",
            "type": "evidence_gap",
            "issue": "An earlier unresolved issue",
            "owner": "research_owner",
            "status": "open",
            "blocking": False,
        }
        planned["open_loops"].append(existing)
        blocked = self.state.advance(planned, first["step_id"], "blocked", reason="Source identity is unresolved")
        self.assertTrue(blocked["valid"], blocked)
        self.assertEqual(blocked["mission"]["open_loops"][0], existing)
        self.assertEqual(len(blocked["mission"]["open_loops"]), 2)
        self.assertEqual(blocked["mission"]["status"], "blocked")
        premature = self.state.advance(blocked["mission"], first["step_id"], "ready")
        self.assertFalse(premature["valid"], premature)
        self.assertIn("unresolved_blocker", {item["code"] for item in premature["errors"]})

    def test_validator_rejects_unknown_skill_and_stage(self):
        mission = minimal_mission(current_stage="teleportation", status="planned")
        mission["route"] = [
            {
                "step_id": "S01",
                "stage": "teleportation",
                "skill": "zju-unregistered-oracle",
                "prerequisites": [],
                "required_gates": [],
                "autonomy_level": "L1",
                "status": "ready",
            }
        ]
        result = self.validator.validate(mission)
        codes = {item["code"] for item in result["errors"]}
        messages = " ".join(item["message"] for item in result["errors"]).lower()

        self.assertFalse(result["valid"], result)
        self.assertIn("unknown_skill", codes)
        self.assertIn("unknown_stage", codes)
        self.assertIn("unregistered", messages)
        self.assertIn("teleportation", messages)

    def test_capability_registry_covers_every_non_director_skill(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        registered = set(registry["skills"])
        installed = {
            path.name
            for path in (ROOT / "skills").iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file() and path.name != "zju-research-director"
        }
        self.assertEqual(registered, installed)

    def test_director_eval_suite_validator_exits_zero(self):
        validator = ROOT / "evals" / "validate_director_suite.py"
        completed = subprocess.run(
            [sys.executable, str(validator)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=utf8_subprocess_env(),
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report.get("valid"), report)

    def test_director_routes_all_twenty_end_to_end_cases(self):
        benchmark = ROOT / "evals" / "run_director_benchmark.py"
        completed = subprocess.run(
            [sys.executable, str(benchmark)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=utf8_subprocess_env(),
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report.get("valid"), report)
        self.assertEqual(report.get("cases"), 20)
        self.assertEqual(report.get("passed"), 20)

    def test_director_scripts_import_no_network_or_process_modules(self):
        forbidden_roots = {
            "ftplib",
            "http",
            "httpx",
            "multiprocessing",
            "requests",
            "socket",
            "smtplib",
            "subprocess",
            "telnetlib",
            "urllib",
        }
        findings = []
        for path in sorted(SCRIPTS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    root = name.split(".", 1)[0]
                    if root in forbidden_roots:
                        findings.append(f"{path.name}:{node.lineno} imports {name}")
        self.assertEqual(findings, [], "Director must remain local and deterministic: " + "; ".join(findings))


if __name__ == "__main__":
    unittest.main()
\n