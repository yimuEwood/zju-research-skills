from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


class SkillResourceLinkTests(unittest.TestCase):
    def test_local_reference_and_script_paths_exist(self):
        missing: list[str] = []
        for skill_path in sorted(SKILLS.glob("*/SKILL.md")):
            content = skill_path.read_text(encoding="utf-8")
            skill_root = skill_path.parent
            resources = set(re.findall(r"(?<![\w$/-])((?:references|scripts)/[A-Za-z0-9_.\-/]+)", content))
            for relative in resources:
                clean = relative.rstrip("`'\".,;:)")
                if not (skill_root / clean).is_file():
                    missing.append(f"{skill_root.name}: {clean}")
        self.assertEqual(missing, [], "missing local resources:\n" + "\n".join(missing))

    def test_cross_skill_script_paths_exist(self):
        missing: list[str] = []
        pattern = re.compile(r"\$(zju-[a-z0-9-]+)/((?:scripts|references)/[A-Za-z0-9_.\-/]+)")
        for skill_path in sorted(SKILLS.glob("*/SKILL.md")):
            content = skill_path.read_text(encoding="utf-8")
            for skill_name, relative in pattern.findall(content):
                clean = relative.rstrip("`'\".,;:)")
                if not (SKILLS / skill_name / clean).is_file():
                    missing.append(f"{skill_path.parent.name}: ${skill_name}/{clean}")
        self.assertEqual(missing, [], "missing cross-skill resources:\n" + "\n".join(missing))

    def test_every_registered_script_exists_and_kind_is_declared(self):
        path = SKILLS / "zju-research-director/references/capability-registry.yaml"
        registry = json.loads(path.read_text(encoding="utf-8"))
        allowed = set(registry["allowed_enums"]["validator_kinds"])
        failures: list[str] = []
        for skill_name, entry in registry["skills"].items():
            for script in entry.get("validators", []):
                if script.get("kind") not in allowed:
                    failures.append(f"{skill_name}: undeclared kind {script.get('kind')!r}")
                relative = script.get("path")
                if not isinstance(relative, str) or not (ROOT / relative).is_file():
                    failures.append(f"{skill_name}: missing registered script {relative!r}")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
