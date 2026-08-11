from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "zju-research-director" / "scripts" / "migrate_mission.py"


def load_module():
    spec = importlib.util.spec_from_file_location("migrate_mission_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def legacy_mission():
    return {
        "schema_version": "1.0",
        "mission_id": "MISSION-LEGACY-001",
        "research_question": "What survives a trust-reducing migration?",
        "artifacts": [
            {
                "artifact_id": "A-VALIDATED",
                "artifact_type": "manuscript",
                "status": "validated",
                "path": "outputs/manuscript.md",
                "content_sha256": "a" * 64,
                "validation": {"status": "passed", "validator": "legacy-self-attestation"},
                "provenance": {"producer": "research_owner"},
            },
            {
                "artifact_id": "A-NO-LOCATOR",
                "artifact_type": "paper_card",
                "status": "created",
            },
        ],
        "route": [
            {
                "step_id": "S01",
                "skill": "zju-paper-reader",
                "expected_outputs": ["paper_card", "bilingual_reader"],
            }
        ],
    }


class MissionMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_migration_downgrades_validation_without_fabricating_hashes(self):
        source = legacy_mission()
        original = copy.deepcopy(source)
        result = self.module.migrate_mission(
            source,
            migrated_at="2026-08-11T15:00:00+08:00",
        )
        migrated = result["mission"]
        report = result["report"]

        self.assertEqual(source, original, "migration must not mutate the source mission")
        self.assertEqual(migrated["schema_version"], "1.1")
        artifact = migrated["artifacts"][0]
        self.assertEqual(artifact["schema_version"], "1.0")
        self.assertEqual(artifact["status"], "created")
        self.assertEqual(artifact["path"], "outputs/manuscript.md")
        self.assertEqual(artifact["created_at"], "2026-08-11T15:00:00+08:00")
        self.assertNotIn("content_sha256", artifact)
        self.assertNotIn("validation", artifact)
        self.assertFalse(artifact["legacy_validation"]["effective"])
        self.assertEqual(artifact["legacy_validation"]["content_sha256"], "a" * 64)
        self.assertEqual(
            artifact["legacy_validation"]["validation"]["validator"],
            "legacy-self-attestation",
        )
        self.assertEqual(artifact["provenance"]["mission_id"], source["mission_id"])
        self.assertEqual(artifact["provenance"]["producer"], "research_owner")
        self.assertEqual(
            artifact["provenance"]["migration"]["trust"],
            "unverified_legacy_metadata",
        )
        self.assertEqual(report["downgraded_validated_artifacts"], ["A-VALIDATED"])
        self.assertEqual(report["legacy_validation_artifacts"], ["A-VALIDATED"])

    def test_artifact_without_locator_becomes_planned_and_unknown_producer_stays_explicit(self):
        result = self.module.migrate_mission(
            legacy_mission(),
            migrated_at="2026-08-11T07:00:00Z",
        )
        artifact = result["mission"]["artifacts"][1]
        self.assertEqual(artifact["status"], "planned")
        self.assertNotIn("path", artifact)
        self.assertNotIn("uri", artifact)
        self.assertNotIn("content_ref", artifact)
        self.assertNotIn("content_sha256", artifact)
        self.assertNotIn("validation", artifact)
        self.assertEqual(artifact["provenance"]["producer"], "legacy_unknown")
        self.assertEqual(
            artifact["provenance"]["migration"]["producer_status"],
            "not_recorded",
        )
        self.assertEqual(
            result["report"]["unresolved_provenance_artifacts"],
            ["A-NO-LOCATOR"],
        )

    def test_existing_legacy_validation_is_always_inert(self):
        mission = legacy_mission()
        mission["artifacts"][1]["legacy_validation"] = {
            "effective": True,
            "claim": "previously trusted",
        }
        migrated = self.module.migrate_mission(
            mission,
            migrated_at="2026-08-11T15:00:00+08:00",
        )["mission"]
        legacy = migrated["artifacts"][1]["legacy_validation"]
        self.assertFalse(legacy["effective"])
        self.assertEqual(legacy["claim"], "previously trusted")
        self.assertIn("must not satisfy", legacy["reason"])

    def test_route_contract_upgrade_and_second_migration_are_idempotent(self):
        first = self.module.migrate_mission(
            legacy_mission(),
            migrated_at="2026-08-11T15:00:00+08:00",
        )
        step = first["mission"]["route"][0]
        self.assertEqual(
            step["required_output_groups"],
            [["paper_card", "bilingual_reader"]],
        )
        self.assertEqual(step["optional_outputs"], [])
        self.assertEqual(first["report"]["route_steps_updated"], ["S01"])

        second = self.module.migrate_mission(
            first["mission"],
            migrated_at="2026-08-12T15:00:00+08:00",
        )
        self.assertEqual(second["mission"], first["mission"])
        self.assertFalse(second["report"]["changed"])
        self.assertEqual(second["report"]["status"], "already_current")
        self.assertEqual(len(second["mission"]["migration_history"]), 1)

    def test_rejects_missing_time_naive_time_and_future_schema(self):
        with self.assertRaises(TypeError):
            self.module.migrate_mission(legacy_mission())
        with self.assertRaisesRegex(self.module.MigrationError, "supplied explicitly"):
            self.module.migrate_mission(legacy_mission(), migrated_at=None)
        with self.assertRaisesRegex(self.module.MigrationError, "UTC offset"):
            self.module.migrate_mission(
                legacy_mission(),
                migrated_at="2026-08-11T15:00:00",
            )
        future = legacy_mission()
        future["schema_version"] = "2.0"
        with self.assertRaisesRegex(self.module.MigrationError, "newer than supported"):
            self.module.migrate_mission(
                future,
                migrated_at="2026-08-11T15:00:00+08:00",
            )

    def test_cli_writes_mission_and_separate_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "mission.json"
            output = temp / "mission-1.1.json"
            report = temp / "migration-report.json"
            source.write_text(json.dumps(legacy_mission()), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--report",
                    str(report),
                    "--migrated-at",
                    "2026-08-11T15:00:00+08:00",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"], "1.1")
            migration_report = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(migration_report["changed"])
            self.assertEqual(migration_report["downgraded_validated_artifacts"], ["A-VALIDATED"])


if __name__ == "__main__":
    unittest.main()
