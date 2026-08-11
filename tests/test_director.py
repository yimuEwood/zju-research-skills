from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
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
        "schema_version": "1.1",
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


def validated_artifact(
    artifact_id,
    artifact_type,
    producer,
    base_dir,
    mission_id="MISSION-CONTRACT-001",
    path=None,
    content_sha256=None,
    producer_step_id=None,
):
    root = Path(base_dir).resolve()
    if path is None:
        relative_path = Path("artifacts") / f"{artifact_id}.txt"
        artifact_path = root / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            f"artifact_id={artifact_id}\nartifact_type={artifact_type}\nproducer={producer}\n",
            encoding="utf-8",
        )
        path = relative_path.as_posix()
    else:
        artifact_path = Path(path)
        if not artifact_path.is_absolute():
            artifact_path = root / artifact_path
    if content_sha256 is None:
        content_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))["skills"]
    registered = registry.get(producer, {})
    registered_validators = registered.get("validators", []) if isinstance(registered, dict) else []
    validator = next(
        (
            item["path"]
            for item in registered_validators
            if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]
        ),
        "tests/fixture-validator.py",
    )
    method = "deterministic"
    command = [sys.executable, validator, "--input", str(path)]
    command_sha256 = hashlib.sha256(
        json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt_id = f"RECEIPT-{artifact_id}"
    checked_at = "2026-08-11T10:05:00+08:00"
    receipt = {
        "receipt_id": receipt_id,
        "artifact_id": artifact_id,
        "content_sha256": content_sha256,
        "validator": validator,
        "validator_version": "test-fixture-v2",
        "command": command,
        "command_sha256": command_sha256,
        "exit_code": 0,
        "checked_at": checked_at,
        "valid": True,
    }
    report_relative = Path("receipts") / f"{artifact_id}.json"
    report_path = root / report_relative
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    provenance = {"mission_id": mission_id, "producer": producer}
    if producer_step_id is not None:
        provenance["producer_step_id"] = producer_step_id
    return {
        "schema_version": "1.0",
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "status": "validated",
        "created_at": "2026-08-11T10:00:00+08:00",
        "path": path,
        "content_sha256": content_sha256,
        "provenance": provenance,
        "validation": {
            "status": "passed",
            "method": method,
            "validator": validator,
            "validator_version": "test-fixture-v2",
            "receipt_id": receipt_id,
            "command": command,
            "command_sha256": command_sha256,
            "exit_code": 0,
            "report_path": report_relative.as_posix(),
            "report_sha256": report_sha256,
            "checked_at": checked_at,
            "content_sha256": content_sha256,
        },
    }


def trusted_validation_attestation(artifact):
    validation = artifact["validation"]
    provenance = artifact["provenance"]
    attestation = {
        "schema_version": "1.0",
        "attestation_id": f"ATTEST-{artifact['artifact_id']}",
        "runner_id": "independent-test-runner",
        "runner_version": "test-runner-v1",
        "trust_domain": "unit-test-fixture",
        "source_channel": "isolated_validation_runner",
        "mission_id": provenance.get("mission_id"),
        "artifact_id": artifact["artifact_id"],
        "artifact_type": artifact["artifact_type"],
        "producer": provenance.get("producer"),
        "producer_step_id": provenance.get("producer_step_id"),
        "content_sha256": artifact["content_sha256"],
        "validation_receipt_id": validation["receipt_id"],
        "validator": validation["validator"],
        "validator_version": validation["validator_version"],
        "command": copy.deepcopy(validation["command"]),
        "command_sha256": validation["command_sha256"],
        "exit_code": validation["exit_code"],
        "report_sha256": validation["report_sha256"],
        "checked_at": validation["checked_at"],
        "attested_at": "2026-08-11T10:06:00+08:00",
        "valid": True,
    }
    attestation["payload_sha256"] = hashlib.sha256(
        json.dumps(attestation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return attestation


def trusted_validation_attestations(mission):
    return [
        trusted_validation_attestation(artifact)
        for artifact in mission.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("status") == "validated"
    ]


def materialize_canonical_route(mission, base_dir=None):
    planner = load_module("director_plan_mission_fixture", SCRIPTS / "plan_mission.py")
    planned = planner.plan_mission(copy.deepcopy(mission), base_dir=base_dir)
    artifacts_by_type = {}
    artifacts_by_id = {
        artifact.get("artifact_id"): artifact
        for artifact in planned.get("artifacts", [])
        if isinstance(artifact, dict)
    }
    for artifact in planned.get("artifacts", []):
        token = str(artifact.get("artifact_type", "")).lower().replace("-", "_").replace(" ", "_")
        artifacts_by_type.setdefault(token, []).append(artifact["artifact_id"])
    for step in planned.get("route", []):
        produced = []
        for group in step.get("required_output_groups", []):
            candidate = next(
                (
                    artifact_id
                    for output_type in group
                    for artifact_id in artifacts_by_type.get(output_type, [])
                ),
                None,
            )
            if candidate is not None:
                produced.append(candidate)
        step["produced_artifact_ids"] = list(dict.fromkeys(produced))
        for artifact_id in step["produced_artifact_ids"]:
            artifact = artifacts_by_id[artifact_id]
            artifact["provenance"]["producer"] = step["skill"]
            artifact["provenance"]["producer_step_id"] = step["step_id"]
            registered_validators = json.loads(REGISTRY.read_text(encoding="utf-8"))["skills"][
                step["skill"]
            ].get("validators", [])
            validator = next(
                item["path"]
                for item in registered_validators
                if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]
            )
            validation = artifact["validation"]
            command = [sys.executable, validator, "--input", str(artifact["path"])]
            validation["validator"] = validator
            validation["command"] = command
            validation["command_sha256"] = hashlib.sha256(
                json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            report = {
                "receipt_id": validation["receipt_id"],
                "artifact_id": artifact["artifact_id"],
                "content_sha256": artifact["content_sha256"],
                "validator": validation["validator"],
                "validator_version": validation["validator_version"],
                "command": validation["command"],
                "command_sha256": validation["command_sha256"],
                "exit_code": validation["exit_code"],
                "checked_at": validation["checked_at"],
                "valid": True,
            }
            report_path = Path(base_dir) / validation["report_path"]
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            validation["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        step["status"] = "completed"
    planned["status"] = "active"
    return planned


def release_candidate(base_dir):
    mission = minimal_mission(
        constraints={
            "autonomy_ceiling": "L2",
            "release_intent": True,
            "release_artifact_ids": ["A01", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10"],
        },
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
        validated_artifact(
            "A01",
            "manuscript",
            "research_owner",
            base_dir,
            mission_id=mission["mission_id"],
        ),
        validated_artifact("A02", "data_inventory", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A03", "statistics_audit", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A04", "reference_audit", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A05", "data_availability", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A06", "scientific_figure", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A07", "figure_manifest", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A08", "figure_qa_report", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A09", "reviewer_report", "research_owner", base_dir, mission_id=mission["mission_id"]),
        validated_artifact("A10", "integrity_audit", "research_owner", base_dir, mission_id=mission["mission_id"]),
    ]
    return materialize_canonical_route(mission, base_dir)


class DirectorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planner = load_module("director_plan_mission_contract", SCRIPTS / "plan_mission.py")
        cls.validator = load_module("director_validate_mission_contract", SCRIPTS / "validate_mission.py")
        cls.merger = load_module("director_merge_artifacts_contract", SCRIPTS / "merge_artifacts.py")
        cls.gates = load_module("director_check_stage_gate_contract", SCRIPTS / "check_stage_gate.py")
        cls.state = load_module("director_advance_mission_contract", SCRIPTS / "advance_mission.py")
        cls.authorization = load_module(
            "director_authorization_contract_integration",
            SCRIPTS / "authorization_contract.py",
        )

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.artifact_root = Path(self.temp_dir.name).resolve()

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

    def test_presentation_plan_requires_materialized_pptx_and_render_qa(self):
        planned = self.planner.plan_mission(minimal_mission(requested_deliverables=["pptx"]))
        deck_step = next(step for step in planned["route"] if step["skill"] == "zju-paper2ppt")

        self.assertIn(["pptx"], deck_step["required_output_groups"])
        self.assertIn(["render_qa_report"], deck_step["required_output_groups"])
        self.assertIn("deck_plan", deck_step["optional_outputs"])

    def test_deck_plan_cannot_complete_a_materialized_pptx_step(self):
        mission = minimal_mission(status="planned", requested_deliverables=["pptx"])
        mission["route"] = [
            {
                "step_id": "S01",
                "stage": "communication",
                "skill": "zju-paper2ppt",
                "prerequisites": [],
                "required_gates": [],
                "autonomy_level": "L1",
                "status": "ready",
                "expected_outputs": ["pptx", "deck_plan", "render_qa_report"],
                "required_output_groups": [["pptx"], ["render_qa_report"]],
                "optional_outputs": ["deck_plan"],
                "produced_artifact_ids": [],
            }
        ]
        deck_plan = validated_artifact(
            "A-DECK-PLAN",
            "deck_plan",
            "zju-paper2ppt",
            self.artifact_root,
            producer_step_id="S01",
        )
        mission["artifacts"] = [deck_plan]

        rejected = self.state.advance(
            mission,
            "S01",
            "completed",
            ["A-DECK-PLAN"],
            [],
            base_dir=self.artifact_root,
        )

        self.assertFalse(rejected["valid"], rejected)
        missing = next(item for item in rejected["errors"] if item["code"] == "missing_required_output")
        self.assertEqual(missing["missing_groups"], [["pptx"], ["render_qa_report"]])

        fake_pptx = validated_artifact(
            "A-PPTX-FAKE",
            "pptx",
            "zju-paper2ppt",
            self.artifact_root,
            producer_step_id="S01",
        )
        rejected_format = self.merger.merge(minimal_mission(), [fake_pptx], base_dir=self.artifact_root)
        self.assertFalse(rejected_format["valid"], rejected_format)
        self.assertIn(
            "content_format",
            {item["code"] for item in rejected_format["errors"][0]["findings"]},
        )

        pptx_path = self.artifact_root / "actual.pptx"
        with zipfile.ZipFile(pptx_path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("ppt/presentation.xml", "<p:presentation/>")
        pptx = validated_artifact(
            "A-PPTX",
            "pptx",
            "zju-paper2ppt",
            self.artifact_root,
            path=str(pptx_path),
            producer_step_id="S01",
        )
        qa = validated_artifact(
            "A-PPTX-QA",
            "render_qa_report",
            "zju-paper2ppt",
            self.artifact_root,
            producer_step_id="S01",
        )
        mission["artifacts"].extend([pptx, qa])
        accepted = self.state.advance(
            mission,
            "S01",
            "completed",
            ["A-PPTX", "A-PPTX-QA"],
            [],
            base_dir=self.artifact_root,
            trusted_validation_receipts=trusted_validation_attestations(mission),
        )
        self.assertTrue(accepted["valid"], accepted)

    def test_sensitive_actions_always_stop_at_human_gates(self):
        cases = {
            "external_action": release_candidate(self.artifact_root),
            "patent_legal": release_candidate(self.artifact_root),
            "ethics": release_candidate(self.artifact_root),
            "submission_release": release_candidate(self.artifact_root),
        }
        cases["ethics"]["constraints"]["ethics_required"] = True

        for gate, mission in cases.items():
            with self.subTest(gate=gate):
                result = self.gates.check_gate(mission, gate, base_dir=self.artifact_root)
                if gate == "submission_release":
                    result = self.gates.check_gate(
                        mission,
                        gate,
                        base_dir=self.artifact_root,
                        trusted_validation_receipts=trusted_validation_attestations(mission),
                    )
                self.assertTrue(result["valid"], result)
                self.assertEqual(result["status"], "requires_human_review", result)
                self.assertFalse(result["passed"])
                self.assertTrue(result.get("human_authority"), result)
                self.assertTrue(result.get("required_actions"), result)

    def test_submission_release_rechecks_full_upstream_chain_and_scoped_authorization(self):
        mission = release_candidate(self.artifact_root)
        validation_attestations = trusted_validation_attestations(mission)
        mission["trusted_validation_receipts"] = copy.deepcopy(validation_attestations)
        untrusted = self.gates.check_gate(
            mission,
            "submission_release",
            base_dir=self.artifact_root,
        )
        self.assertEqual(untrusted["status"], "blocked", untrusted)
        self.assertIn("trusted-runner attestation", " ".join(untrusted["blockers"]))
        pending = self.gates.check_gate(
            mission,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_validation_receipts=validation_attestations,
        )
        self.assertEqual(pending["status"], "requires_human_review", pending)
        self.assertEqual(
            pending["applicable_upstream_gates"],
            ["evidence", "data", "analysis", "reference", "claim", "artifact", "integrity", "reproducibility"],
        )

        artifact_ids = list(mission["constraints"]["release_artifact_ids"])
        mission["decisions"] = [
            {
                "decision_id": "DEC-RELEASE-001",
                "approval_receipt_id": "APR-RELEASE-001",
                "decision_type": "submission_release",
                "status": "approved",
                "authorized_by": "research-owner-001",
                "authority_role": "corresponding_author",
                "scope": {"mission_id": mission["mission_id"], "artifact_ids": artifact_ids},
                "target": "submission_package",
                "action": "submit",
                "issued_at": "2026-08-11T12:00:00+08:00",
                "expires_at": "2099-08-11T12:00:00+08:00",
                "state_sha256": self.gates.authorization_state_hash(mission, "submission_release"),
            }
        ]
        decision = mission["decisions"][0]
        receipt = {
            "schema_version": "1.0",
            "receipt_id": decision["approval_receipt_id"],
            "decision_id": decision["decision_id"],
            "gate": "submission_release",
            "mission_id": mission["mission_id"],
            "authorized_by": decision["authorized_by"],
            "authority_role": decision["authority_role"],
            "target": decision["target"],
            "action": decision["action"],
            "scope": copy.deepcopy(decision["scope"]),
            "state_sha256": decision["state_sha256"],
            "issued_at": decision["issued_at"],
            "expires_at": decision["expires_at"],
            "source_channel": "user_confirmed_input",
            "source_reference": "test-trusted-channel",
        }
        receipt["payload_sha256"] = self.authorization.receipt_sha256(receipt)

        self_reported = self.gates.check_gate(
            mission,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_validation_receipts=validation_attestations,
        )
        self.assertEqual(self_reported["status"], "requires_human_review", self_reported)
        self.assertIn("trusted receipt channel", " ".join(self_reported["warnings"]))

        passed = self.gates.check_gate(
            mission,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_receipts=[receipt],
            trusted_validation_receipts=validation_attestations,
        )
        self.assertEqual(passed["status"], "passed", passed)
        self.assertTrue(passed["passed"])
        self.assertEqual(passed["approval_receipt_id"], receipt["receipt_id"])

        route_tampered = copy.deepcopy(mission)
        route_tampered["route"][-1]["required_output_groups"] = [["deck_plan"]]
        self.assertNotEqual(
            self.gates.authorization_state_hash(route_tampered, "submission_release"),
            decision["state_sha256"],
        )
        route_rejected = self.gates.check_gate(
            route_tampered,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_receipts=[receipt],
            trusted_validation_receipts=validation_attestations,
        )
        self.assertEqual(route_rejected["status"], "blocked", route_rejected)
        self.assertIn("canonical route contract", " ".join(route_rejected["blockers"]))

        forged_attestations = copy.deepcopy(validation_attestations)
        forged_attestations[0]["report_sha256"] = "0" * 64
        forged_validation = self.gates.check_gate(
            mission,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_receipts=[receipt],
            trusted_validation_receipts=forged_attestations,
        )
        self.assertEqual(forged_validation["status"], "blocked", forged_validation)
        self.assertIn("trusted-runner attestation", " ".join(forged_validation["blockers"]))

        mission["claims"][0]["statement"] = "The tested intervention changed a different outcome."
        stale = self.gates.check_gate(
            mission,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_receipts=[receipt],
            trusted_validation_receipts=validation_attestations,
        )
        self.assertEqual(stale["status"], "requires_human_review", stale)
        self.assertIn("current gate-relevant state", " ".join(stale["warnings"]))

    def test_submission_release_rejects_unvalidated_declared_payload(self):
        mission = release_candidate(self.artifact_root)
        release_artifact = next(item for item in mission["artifacts"] if item["artifact_id"] == "A01")
        release_artifact["status"] = "created"
        release_artifact.pop("validation")
        release_artifact.pop("content_sha256")

        result = self.gates.check_gate(mission, "submission_release", base_dir=self.artifact_root)

        self.assertEqual(result["status"], "blocked", result)
        blockers = " ".join(result["blockers"])
        self.assertIn("A01", blockers)
        self.assertIn("not validated", blockers)

    def test_submission_release_cannot_self_report_a_narrower_payload(self):
        mission = release_candidate(self.artifact_root)
        mission["constraints"]["release_artifact_ids"] = ["A03"]

        result = self.gates.check_gate(
            mission,
            "submission_release",
            base_dir=self.artifact_root,
        )

        self.assertEqual(result["status"], "blocked", result)
        self.assertIn(["manuscript"], result["required_release_output_groups"])
        blockers = " ".join(result["blockers"])
        self.assertIn("requested-deliverable output groups", blockers)
        self.assertIn("manuscript", blockers)

    def test_release_groups_cover_presentation_and_scientific_figure(self):
        presentation = release_candidate(self.artifact_root)
        presentation["requested_deliverables"] = ["pptx"]
        pptx_path = self.artifact_root / "release-deck.pptx"
        with zipfile.ZipFile(pptx_path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("ppt/presentation.xml", "<p:presentation/>")
        presentation["artifacts"].extend([
            validated_artifact("A-PRES-FULL", "full_text", "research_owner", self.artifact_root),
            validated_artifact("A-PRES-CARD", "paper_card", "research_owner", self.artifact_root),
            validated_artifact("A-PRES", "pptx", "research_owner", self.artifact_root, path=str(pptx_path)),
            validated_artifact("A-PRES-QA", "render_qa_report", "research_owner", self.artifact_root),
        ])
        presentation["constraints"]["release_artifact_ids"] = ["A-PRES", "A-PRES-QA"]
        presentation = materialize_canonical_route(presentation, self.artifact_root)
        presentation_result = self.gates.check_gate(
            presentation,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_validation_receipts=trusted_validation_attestations(presentation),
        )
        self.assertEqual(presentation_result["status"], "requires_human_review", presentation_result)
        self.assertEqual(
            presentation_result["required_release_output_groups"],
            [["pptx"], ["render_qa_report"]],
        )
        presentation["constraints"]["release_artifact_ids"] = ["A-PRES"]
        missing_qa = self.gates.check_gate(
            presentation,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_validation_receipts=trusted_validation_attestations(presentation),
        )
        self.assertEqual(missing_qa["status"], "blocked", missing_qa)

        figure = release_candidate(self.artifact_root)
        figure["requested_deliverables"] = ["figure"]
        figure["constraints"]["release_artifact_ids"] = ["A06", "A07", "A08"]
        figure = materialize_canonical_route(figure, self.artifact_root)
        figure_result = self.gates.check_gate(
            figure,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_validation_receipts=trusted_validation_attestations(figure),
        )
        self.assertEqual(figure_result["status"], "requires_human_review", figure_result)
        self.assertEqual(
            figure_result["required_release_output_groups"],
            [["scientific_figure"], ["figure_manifest"], ["figure_qa_report"]],
        )
        figure["constraints"]["release_artifact_ids"] = ["A06", "A08"]
        missing_manifest = self.gates.check_gate(
            figure,
            "submission_release",
            base_dir=self.artifact_root,
            trusted_validation_receipts=trusted_validation_attestations(figure),
        )
        self.assertEqual(missing_manifest["status"], "blocked", missing_manifest)

    def test_missing_evidence_blocks_claim_and_release(self):
        mission = release_candidate(self.artifact_root)
        mission["evidence_records"] = []
        for gate in ("claim", "submission_release"):
            with self.subTest(gate=gate):
                result = self.gates.check_gate(mission, gate, base_dir=self.artifact_root)
                self.assertEqual(result["status"], "blocked", result)
                self.assertFalse(result["passed"])
                self.assertTrue(result["blockers"])
                self.assertIn("evidence", " ".join(result["blockers"]).lower())

    def test_experiment_presence_alone_cannot_pass_data_gate(self):
        mission = minimal_mission(
            experiments=[
                {
                    "run_id": "RUN-SELF-REPORTED",
                    "experimental_unit": "sample",
                    "status": "completed",
                }
            ]
        )
        result = self.gates.check_gate(mission, "data", base_dir=self.artifact_root)
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("content-bound", " ".join(result["blockers"]))

    def test_claim_boolean_cannot_replace_reference_audit(self):
        mission = minimal_mission(
            claims=[
                {
                    "claim_id": "C-SELF-REPORTED",
                    "status": "supported",
                    "citation_verified": True,
                }
            ]
        )
        result = self.gates.check_gate(mission, "reference", base_dir=self.artifact_root)
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("reference audit", " ".join(result["blockers"]))

    def test_artifact_collision_is_transactional_and_never_overwrites(self):
        mission = release_candidate(self.artifact_root)
        original = copy.deepcopy(mission["artifacts"][0])
        original_ledger = copy.deepcopy(mission["artifacts"])
        collision = {**original, "artifact_type": "manuscript_section"}
        result = self.merger.merge(mission, [collision], base_dir=self.artifact_root)

        self.assertFalse(result["valid"], result)
        self.assertEqual(result["mission"]["artifacts"], original_ledger)
        self.assertEqual(result["merged_ids"], [])
        self.assertTrue(result["conflicts"], result)
        self.assertEqual(result["conflicts"][0]["code"], "id_collision")
        self.assertEqual(result["conflicts"][0]["artifact_id"], original["artifact_id"])

    def test_artifact_merge_preserves_open_loops(self):
        mission = release_candidate(self.artifact_root)
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
            "schema_version": "1.0",
            "artifact_id": "A11",
            "artifact_type": "reviewer_report",
            "status": "created",
            "created_at": "2026-08-11T10:00:00+08:00",
            "path": "artifacts/A11.txt",
            "provenance": {
                "mission_id": mission["mission_id"],
                "producer": "zju-reviewer",
                "producer_step_id": "S06",
            },
        }
        result = self.merger.merge(mission, [incoming], base_dir=self.artifact_root)

        self.assertTrue(result["valid"], result)
        self.assertEqual(result["mission"]["open_loops"], [loop])
        self.assertEqual(result["open_loops_preserved"], [loop])
        self.assertEqual(result["merged_ids"], ["A11"])

    def test_artifact_merge_rejects_self_attested_validation_transactionally(self):
        mission = release_candidate(self.artifact_root)
        self_attested = {
            "schema_version": "1.0",
            "artifact_id": "A02",
            "artifact_type": "reviewer_report",
            "status": "validated",
            "created_at": "2026-08-11T10:00:00+08:00",
            "path": "artifacts/reviewer-report.md",
            "provenance": {
                "mission_id": mission["mission_id"],
                "producer": "zju-reviewer",
                "producer_step_id": "S06",
            },
        }
        original = copy.deepcopy(mission)

        result = self.merger.merge(mission, [self_attested], base_dir=self.artifact_root)

        self.assertFalse(result["valid"], result)
        self.assertEqual(result["mission"], original)
        self.assertEqual(result["errors"][0]["code"], "artifact_contract")
        finding_codes = {item["code"] for item in result["errors"][0]["findings"]}
        self.assertIn("required_hash", finding_codes)
        self.assertIn("type", finding_codes)

    def test_artifact_merge_rejects_wrong_mission_and_undeclared_output(self):
        mission = release_candidate(self.artifact_root)
        forged = validated_artifact(
            "A02",
            "manuscript",
            "zju-reviewer",
            self.artifact_root,
            mission_id="MISSION-OTHER",
            producer_step_id="S06",
        )

        result = self.merger.merge(mission, [forged], base_dir=self.artifact_root)

        self.assertFalse(result["valid"], result)
        findings = result["errors"][0]["findings"]
        self.assertEqual({item["code"] for item in findings}, {"mission_mismatch", "undeclared_output"})

    def test_artifact_validation_hash_must_bind_to_current_content(self):
        mission = release_candidate(self.artifact_root)
        artifact = validated_artifact(
            "A02",
            "reviewer_report",
            "zju-reviewer",
            self.artifact_root,
            mission_id=mission["mission_id"],
            producer_step_id="S06",
        )
        artifact["validation"]["content_sha256"] = "b" * 64

        result = self.merger.merge(mission, [artifact], base_dir=self.artifact_root)

        self.assertFalse(result["valid"], result)
        finding_codes = {item["code"] for item in result["errors"][0]["findings"]}
        self.assertEqual(finding_codes, {"hash_mismatch"})

    def test_validation_receipt_requires_every_artifact_and_command_binding(self):
        required_bindings = [
            "receipt_id",
            "artifact_id",
            "content_sha256",
            "validator",
            "validator_version",
            "command",
            "command_sha256",
            "exit_code",
            "checked_at",
            "valid",
        ]
        for index, field in enumerate(required_bindings, 1):
            with self.subTest(field=field):
                artifact = validated_artifact(
                    f"A-RECEIPT-{index:02d}",
                    "reviewer_report",
                    "research_owner",
                    self.artifact_root,
                )
                report_path = self.artifact_root / artifact["validation"]["report_path"]
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report.pop(field)
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                artifact["validation"]["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()

                rejected = self.merger.merge(
                    minimal_mission(),
                    [artifact],
                    base_dir=self.artifact_root,
                )
                self.assertFalse(rejected["valid"], rejected)
                findings = rejected["errors"][0]["findings"]
                self.assertIn(
                    f"validation.report.{field}",
                    {item["path"] for item in findings if item["code"] == "validation_report_required"},
                )

        for field, lookalike in (("valid", 1), ("exit_code", False)):
            with self.subTest(field=field, lookalike=lookalike):
                artifact = validated_artifact(
                    f"A-TYPE-{field.upper()}",
                    "reviewer_report",
                    "research_owner",
                    self.artifact_root,
                )
                report_path = self.artifact_root / artifact["validation"]["report_path"]
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report[field] = lookalike
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                artifact["validation"]["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
                rejected = self.merger.merge(
                    minimal_mission(),
                    [artifact],
                    base_dir=self.artifact_root,
                )
                self.assertFalse(rejected["valid"], rejected)
                self.assertIn(
                    "validation_report_binding_mismatch",
                    {item["code"] for item in rejected["errors"][0]["findings"]},
                )

        artifact = validated_artifact(
            "A-COMMAND-TAMPER",
            "reviewer_report",
            "research_owner",
            self.artifact_root,
        )
        artifact["validation"]["command"].append("--unreported-extra-argument")
        rejected = self.merger.merge(
            minimal_mission(),
            [artifact],
            base_dir=self.artifact_root,
        )
        finding_codes = {item["code"] for item in rejected["errors"][0]["findings"]}
        self.assertIn("command_hash_mismatch", finding_codes)

    def test_artifact_and_receipt_paths_cannot_escape_base_dir(self):
        sandbox = self.artifact_root / "sandbox"
        sandbox.mkdir()
        artifact = validated_artifact(
            "A-PATH-ESCAPE",
            "reviewer_report",
            "research_owner",
            sandbox,
        )
        original_path = sandbox / artifact["path"]
        outside_artifact = self.artifact_root / "outside-artifact.txt"
        outside_artifact.write_bytes(original_path.read_bytes())
        artifact["path"] = "../outside-artifact.txt"

        rejected_artifact = self.merger.merge(
            minimal_mission(),
            [artifact],
            base_dir=sandbox,
        )
        artifact_codes = {item["code"] for item in rejected_artifact["errors"][0]["findings"]}
        self.assertIn("path_escape", artifact_codes)

        receipt_escape = validated_artifact(
            "A-RECEIPT-ESCAPE",
            "reviewer_report",
            "research_owner",
            sandbox,
        )
        original_receipt = sandbox / receipt_escape["validation"]["report_path"]
        outside_receipt = self.artifact_root / "outside-receipt.json"
        outside_receipt.write_bytes(original_receipt.read_bytes())
        receipt_escape["validation"]["report_path"] = "../outside-receipt.json"
        receipt_escape["validation"]["report_sha256"] = hashlib.sha256(outside_receipt.read_bytes()).hexdigest()

        rejected_receipt = self.merger.merge(
            minimal_mission(),
            [receipt_escape],
            base_dir=sandbox,
        )
        receipt_findings = rejected_receipt["errors"][0]["findings"]
        self.assertIn(
            "validation.report_path",
            {item["path"] for item in receipt_findings if item["code"] == "path_escape"},
        )

    def test_state_transition_requires_validated_outputs_and_passed_gates(self):
        planned = self.planner.plan_mission(minimal_mission())
        first = planned["route"][0]
        rejected = self.state.advance(planned, first["step_id"], "completed", base_dir=self.artifact_root)
        self.assertFalse(rejected["valid"], rejected)
        codes = {item["code"] for item in rejected["errors"]}
        self.assertIn("missing_output", codes)
        self.assertIn("missing_gate_result", codes)

        artifact = validated_artifact(
            "A-SEARCH-01",
            first["expected_outputs"][0],
            first["skill"],
            self.artifact_root,
            mission_id=planned["mission_id"],
            producer_step_id=first["step_id"],
        )
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
        accepted = self.state.advance(
            planned,
            first["step_id"],
            "completed",
            [artifact["artifact_id"]],
            gates,
            base_dir=self.artifact_root,
            trusted_validation_receipts=trusted_validation_attestations(planned),
        )
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
            validated_artifact(
                "A-CARD-01",
                "paper_card",
                "zju-paper-reader",
                self.artifact_root,
                mission_id=mission["mission_id"],
                producer_step_id="S01",
            )
        ]
        forged = [{"gate": "evidence", "status": "passed", "passed": True, "blockers": [], "warnings": []}]
        result = self.state.advance(
            mission,
            "S01",
            "completed",
            ["A-CARD-01"],
            forged,
            base_dir=self.artifact_root,
        )
        self.assertFalse(result["valid"], result)
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("gate_result_mismatch", codes)
        self.assertIn("gate_not_passed", codes)

    def test_noncompletion_transition_rejects_gate_ledger_injection(self):
        mission = minimal_mission(status="planned")
        mission["mission_required_gates"] = ["submission_release"]
        mission["route"] = [
            {
                "step_id": "S01",
                "stage": "reading",
                "skill": "zju-paper-reader",
                "prerequisites": [],
                "required_gates": [],
                "autonomy_level": "L1",
                "status": "ready",
                "expected_outputs": [],
                "required_output_groups": [],
                "optional_outputs": [],
                "produced_artifact_ids": [],
            }
        ]
        forged = [{"gate": "submission_release", "status": "passed", "passed": True}]

        result = self.state.advance(
            mission,
            "S01",
            "running",
            gate_results=forged,
            base_dir=self.artifact_root,
        )

        self.assertFalse(result["valid"], result)
        self.assertIn("unexpected_gate_results", {item["code"] for item in result["errors"]})
        self.assertEqual(result["mission"].get("gate_ledger", []), [])

    def test_mission_completion_recomputes_required_gates_instead_of_trusting_ledger(self):
        mission = minimal_mission(status="planned")
        mission["mission_required_gates"] = ["evidence"]
        mission["gate_ledger"] = [
            {
                "gate_event_id": "GATE-FORGED-001",
                "step_id": "S01",
                "gate": "evidence",
                "status": "passed",
                "checked_inputs": [],
                "blockers": [],
                "warnings": [],
            }
        ]
        mission["route"] = [
            {
                "step_id": "S01",
                "stage": "reading",
                "skill": "zju-paper-reader",
                "prerequisites": [],
                "required_gates": [],
                "autonomy_level": "L1",
                "status": "ready",
                "expected_outputs": [],
                "required_output_groups": [],
                "optional_outputs": [],
                "produced_artifact_ids": [],
            }
        ]

        result = self.state.advance(mission, "S01", "completed", base_dir=self.artifact_root)

        self.assertTrue(result["valid"], result)
        self.assertEqual(result["mission"]["status"], "active")
        self.assertEqual(result["mission"]["next_action"]["action"], "evaluate_mission_gates")
        latest = result["mission"]["gate_ledger"][-1]
        self.assertEqual(latest["gate"], "evidence")
        self.assertEqual(latest["status"], "blocked")
        self.assertRegex(latest["checked_state_sha256"], r"^[0-9a-f]{64}$")

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
        blocked = self.state.advance(
            planned,
            first["step_id"],
            "blocked",
            reason="Source identity is unresolved",
            base_dir=self.artifact_root,
        )
        self.assertTrue(blocked["valid"], blocked)
        self.assertEqual(blocked["mission"]["open_loops"][0], existing)
        self.assertEqual(len(blocked["mission"]["open_loops"]), 2)
        self.assertEqual(blocked["mission"]["status"], "blocked")
        premature = self.state.advance(
            blocked["mission"],
            first["step_id"],
            "ready",
            base_dir=self.artifact_root,
        )
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

    def test_validator_requires_explicit_migration_for_legacy_mission(self):
        legacy = minimal_mission(schema_version="1.0")
        result = self.validator.validate(legacy, base_dir=self.artifact_root)
        self.assertFalse(result["valid"], result)
        self.assertTrue(
            any(item["code"] == "migration_required" for item in result["errors"]),
            result,
        )

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
