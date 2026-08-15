from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/zju-research-director/scripts/resolve_executor.py"
REGISTRY = ROOT / "skills/zju-research-director/references/executor-registry.yaml"


def load_module():
    spec = importlib.util.spec_from_file_location("executor_resolver", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def provider(
    provider_id: str,
    *,
    capabilities: list[str],
    platforms: list[str] | None = None,
    accepts: list[str] | None = None,
    produces: list[str] | None = None,
    priority: int = 50,
) -> dict[str, object]:
    return {
        "provider_id": provider_id,
        "kind": "skill",
        "capabilities": capabilities,
        "platforms": platforms or ["any"],
        "accepts": accepts or ["input"],
        "produces": produces or ["output"],
        "priority": priority,
        "availability_probe": {"type": "inventory_key", "key": provider_id},
        "invocation_hint": f"Invoke {provider_id} through its host binding.",
        "output_adapter": "adapt:test",
        "license_status": "test-only",
    }


class ExecutorRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = load_module()
        cls.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_registry_is_valid_json_yaml_with_required_capabilities(self):
        validated = self.resolver.validate_registry(self.registry)
        self.assertEqual(validated["schema_version"], "1.0")
        self.assertEqual(
            set(validated["logical_capabilities"]),
            {
                "literature_search",
                "fulltext_retrieval",
                "reference_resolution",
                "structured_fulltext_extraction",
                "pdf_extraction",
                "dataset_profiling",
                "statistical_analysis",
                "scientific_figure",
                "document_generation",
                "presentation",
                "citation_library",
                "chemistry_lookup",
                "omics_analysis",
                "materials_computation",
                "drug_discovery",
            },
        )
        self.assertTrue(all(item["availability_probe"]["type"] == "inventory_key" for item in validated["providers"]))

    def test_selection_is_deterministic_when_registry_and_inventory_order_change(self):
        inventory_a = {
            "platform": "codex",
            "available": [
                "skill.research.generic_executor",
                "skill.academic.openalex",
                "skill.academic.paper_lookup",
            ],
        }
        inventory_b = {"platform": "codex", "available": list(reversed(inventory_a["available"]))}
        requirement = [{"capability": "literature_search", "produces": ["bibliographic_records"]}]
        forward = self.resolver.resolve_executors(self.registry, inventory_a, requirement)
        reversed_registry = {**self.registry, "providers": list(reversed(self.registry["providers"]))}
        reverse = self.resolver.resolve_executors(reversed_registry, inventory_b, requirement)
        self.assertEqual(forward["selected"], reverse["selected"])
        self.assertEqual(forward["selected"][0]["provider_id"], "skill.academic.paper_lookup")
        self.assertFalse(forward["execution_performed"])

    def test_platform_filtering_uses_declared_platforms(self):
        registry = {
            "logical_capabilities": ["presentation"],
            "providers": [
                provider("tool.codex.deck", capabilities=["presentation"], platforms=["codex"]),
                provider("tool.claude.deck", capabilities=["presentation"], platforms=["claude-code"]),
            ],
        }
        inventory = {"platform": "claude-code", "available": ["tool.codex.deck", "tool.claude.deck"]}
        result = self.resolver.resolve_executors(registry, inventory, ["presentation"])
        self.assertEqual(result["selected"][0]["provider_id"], "tool.claude.deck")
        codex = next(
            item
            for item in result["candidates"][0]["providers"]
            if item["provider_id"] == "tool.codex.deck"
        )
        self.assertIn("platform_mismatch", codex["rejection_reasons"])

    def test_missing_capability_is_unresolved(self):
        result = self.resolver.resolve_executors(
            self.registry,
            {"platform": "opencode", "available": []},
            ["genome_assembly"],
        )
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["unresolved"][0]["reason"], "unknown_capability")

    def test_unavailable_provider_is_never_selected(self):
        result = self.resolver.resolve_executors(
            self.registry,
            {"platform": "codex", "available": ["skill.research.generic_executor"]},
            [{"capability": "statistical_analysis", "produces": ["result_registry"]}],
        )
        self.assertEqual(result["selected"][0]["provider_id"], "skill.research.generic_executor")
        local = next(
            item
            for item in result["candidates"][0]["providers"]
            if item["provider_id"] == "script.zju.statistics.execute_analysis"
        )
        self.assertFalse(local["eligible"])
        self.assertIn("unavailable", local["rejection_reasons"])

    def test_narrow_provider_beats_higher_priority_generic_provider(self):
        registry = {
            "logical_capabilities": ["statistical_analysis", "scientific_figure"],
            "providers": [
                provider(
                    "skill.generic",
                    capabilities=["statistical_analysis", "scientific_figure"],
                    accepts=["*"],
                    produces=["*"],
                    priority=100,
                ),
                provider(
                    "script.narrow",
                    capabilities=["statistical_analysis"],
                    accepts=["analysis_execution_spec", "tabular_data"],
                    produces=["result_registry"],
                    priority=10,
                ),
            ],
        }
        inventory = {"platform": "codex", "available": ["skill.generic", "script.narrow"]}
        requirement = [{
            "capability": "statistical_analysis",
            "accepts": ["analysis_execution_spec"],
            "produces": ["result_registry"],
        }]
        result = self.resolver.resolve_executors(registry, inventory, requirement)
        self.assertEqual(result["selected"][0]["provider_id"], "script.narrow")

    def test_priority_breaks_tie_between_equally_narrow_providers(self):
        registry = {
            "logical_capabilities": ["citation_library"],
            "providers": [
                provider("skill.low", capabilities=["citation_library"], priority=10),
                provider("skill.high", capabilities=["citation_library"], priority=20),
            ],
        }
        inventory = {"platform": "codex", "available": ["skill.low", "skill.high"]}
        result = self.resolver.resolve_executors(registry, inventory, ["citation_library"])
        self.assertEqual(result["selected"][0]["provider_id"], "skill.high")

    def test_cli_writes_resolution_and_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inventory = tmp_path / "inventory.json"
            output = tmp_path / "resolution.json"
            inventory.write_text(
                json.dumps({
                    "platform": "codex",
                    "available": ["script.zju.paper_reader.prepare_source"],
                }),
                encoding="utf-8",
            )
            exit_code = self.resolver.main([
                "--inventory", str(inventory),
                "--capability", "pdf_extraction",
                "--output", str(output),
            ])
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["selected"][0]["provider_id"], "script.zju.paper_reader.prepare_source")
            self.assertFalse(payload["execution_performed"])

    def test_new_native_executors_are_narrowly_routable(self):
        cases = [
            (
                "reference_resolution",
                "reference_resolution",
                "script.zju.reference_audit.resolve_reference",
            ),
            (
                "structured_fulltext_extraction",
                "jats_reader_bundle",
                "script.zju.paper_reader.parse_jats",
            ),
            (
                "dataset_profiling",
                "dataset_profile",
                "script.zju.statistics.profile_dataset",
            ),
            (
                "document_generation",
                "research_docx",
                "script.zju.writing.build_research_docx",
            ),
            (
                "presentation",
                "presentation_file",
                "script.zju.paper2ppt.build_presentation",
            ),
            (
                "omics_analysis",
                "omics_screen",
                "script.pack.omics.screen",
            ),
            (
                "materials_computation",
                "structure_analysis",
                "script.pack.materials.analyze_structure",
            ),
            (
                "drug_discovery",
                "candidate_priority",
                "script.pack.drug.prioritize_candidates",
            ),
        ]
        for capability, produced, provider_id in cases:
            with self.subTest(capability=capability):
                result = self.resolver.resolve_executors(
                    self.registry,
                    {"platform": "codex", "available": [provider_id]},
                    [{"capability": capability, "produces": [produced]}],
                )
                self.assertEqual(result["selected"][0]["provider_id"], provider_id)
                self.assertFalse(result["execution_performed"])

    def test_materials_cif_requires_the_pymatgen_inventory_key(self):
        native_only = self.resolver.resolve_executors(
            self.registry,
            {"platform": "codex", "available": ["script.pack.materials.analyze_structure"]},
            [{"capability": "materials_computation", "accepts": ["cif"], "produces": ["structure_analysis"]}],
        )
        self.assertEqual(native_only["selected"], [])
        self.assertEqual(native_only["unresolved"][0]["reason"], "no_available_compatible_provider")
        with_pymatgen = self.resolver.resolve_executors(
            self.registry,
            {"platform": "codex", "available": ["script.pack.materials.analyze_structure.pymatgen"]},
            [{"capability": "materials_computation", "accepts": ["cif"], "produces": ["structure_analysis"]}],
        )
        self.assertEqual(
            with_pymatgen["selected"][0]["provider_id"],
            "script.pack.materials.analyze_structure_pymatgen",
        )


if __name__ == "__main__":
    unittest.main()
