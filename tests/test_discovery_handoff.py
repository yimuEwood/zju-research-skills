from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class DiscoveryHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normalizer = load_module(
            "handoff_normalizer", "skills/zju-literature-search/scripts/normalize_records.py"
        )
        cls.evidence = load_module(
            "handoff_evidence", "skills/zju-evidence-synthesis/scripts/validate_evidence_table.py"
        )
        cls.hypotheses = load_module(
            "handoff_hypotheses", "skills/zju-hypothesis-design/scripts/validate_hypothesis_set.py"
        )

    def test_record_ids_are_order_independent_and_existing_id_is_preserved(self):
        records = [
            {"record_id": "REC-KEEP-01", "doi": "10.1000/existing", "title": "Existing"},
            {"title": "A generated study", "authors": ["Zhang Wei"], "year": 2025},
            {"title": "A generated study", "authors": ["Li Ming"], "year": 2025},
        ]
        forward = self.normalizer.deduplicate(records)
        reverse = self.normalizer.deduplicate(list(reversed(records)))

        def keyed(rows):
            return {
                row["doi"] or f"{row['title']}|{'|'.join(row['authors'])}|{row['year']}": row["record_id"]
                for row in rows
            }

        self.assertEqual(keyed(forward), keyed(reverse))
        self.assertEqual(keyed(forward)["10.1000/existing"], "REC-KEEP-01")
        generated = [row["record_id"] for row in forward if not row["doi"]]
        self.assertEqual(len(generated), len(set(generated)))
        self.assertTrue(all(record_id.startswith("REC-") for record_id in generated))

    def test_duplicate_supplied_record_id_cannot_collide_across_records(self):
        records = [
            {"record_id": "REC-COLLIDE", "doi": "10.1000/b", "title": "B"},
            {"record_id": "REC-COLLIDE", "doi": "10.1000/a", "title": "A"},
        ]
        forward = self.normalizer.deduplicate(records)
        reverse = self.normalizer.deduplicate(list(reversed(records)))
        forward_map = {row["doi"]: row["record_id"] for row in forward}
        reverse_map = {row["doi"]: row["record_id"] for row in reverse}
        self.assertEqual(forward_map, reverse_map)
        self.assertEqual(len(set(forward_map.values())), 2)
        self.assertIn("REC-COLLIDE", forward_map.values())
        reassigned = next(row for row in forward if row["record_id"] != "REC-COLLIDE")
        self.assertTrue(any("record_id_collision" in warning for warning in reassigned["warnings"]))

    @staticmethod
    def evidence_row(record_id: str, study_id: str, role: str) -> dict[str, object]:
        return {
            "record_id": record_id,
            "study_id": study_id,
            "report_id": f"R-{study_id}",
            "citation_id": f"10.1000/{study_id.lower()}",
            "design": "controlled experiment",
            "population_or_system": "test system",
            "sample_and_unit": "12 independent samples",
            "intervention_or_exposure": "A",
            "comparator": "B",
            "outcome": "Y",
            "time_point": "day 7",
            "effect_estimate": "reported",
            "uncertainty": "reported interval",
            "risk_of_bias": "some_concerns",
            "source_anchor": "p. 4, Fig. 2",
            "claim_id": "CLM-1",
            "evidence_role": role,
            "directness": "direct",
        }

    def test_evidence_claim_relations_match_row_roles(self):
        bundle = {
            "rows": [
                self.evidence_row("REC-1", "S1", "supports"),
                self.evidence_row("REC-2", "S2", "contradicts"),
            ],
            "claims": [{
                "claim_id": "CLM-1",
                "certainty": "low",
                "supporting_study_ids": ["S1"],
                "contradicting_study_ids": ["S2"],
                "contextual_study_ids": [],
            }],
            "conflicts": [{"conflict_id": "CF-1", "claim_id": "CLM-1", "study_ids": ["S1", "S2"]}],
        }
        self.assertTrue(self.evidence.validate(bundle)["valid"], self.evidence.validate(bundle))

        reversed_relations = {**bundle, "claims": [{
            **bundle["claims"][0],
            "supporting_study_ids": ["S2"],
            "contradicting_study_ids": ["S1"],
        }]}
        invalid = self.evidence.validate(reversed_relations)
        self.assertFalse(invalid["valid"])
        self.assertTrue(any("matching supports row" in item["message"] for item in invalid["findings"]))

        missing_record_id = dict(bundle["rows"][0])
        missing_record_id.pop("record_id")
        self.assertFalse(self.evidence.validate([missing_record_id])["valid"])

    @staticmethod
    def hypothesis_payload() -> dict[str, object]:
        return {
            "schema_version": "2.0",
            "known_evidence_ids": ["E1", "E2"],
            "hypotheses": [
                {
                    "hypothesis_id": "H1", "mechanism": "M1", "assumptions": ["A1"],
                    "evidence_ids": ["E1"], "contradicting_evidence_ids": ["E2"],
                    "unique_predictions": ["P1"], "falsifiers": ["F1"],
                    "boundary_conditions": ["tested system"], "alternative_ids": ["H2"],
                },
                {
                    "hypothesis_id": "H2", "mechanism": "M2", "assumptions": ["A2"],
                    "evidence_ids": ["E2"], "contradicting_evidence_ids": ["E1"],
                    "unique_predictions": ["P2"], "falsifiers": ["F2"],
                    "boundary_conditions": ["tested system"], "alternative_ids": ["H1"],
                },
            ],
            "experiments": [{
                "experiment_id": "X1", "hypothesis_ids": ["H1", "H2"],
                "experimental_unit": "sample", "intervention": "perturbation", "control": "vehicle",
                "primary_outcome": "signal", "decision_rule": "predefined contrast",
                "predicted_outcomes": {"H1": "increase", "H2": "no_change"},
                "inconclusive_region": "within measurement noise",
                "feasibility": {"technical": 0.9}, "cost_level": 2, "time_level": 2,
                "orthogonal_measurement": True,
            }],
        }

    def test_hypotheses_reject_unknown_evidence_and_accept_evidence_map(self):
        payload = self.hypothesis_payload()
        self.assertTrue(self.hypotheses.validate(payload)["valid"], self.hypotheses.validate(payload))

        unknown = self.hypothesis_payload()
        unknown["hypotheses"][0]["evidence_ids"] = ["E-NOT-IN-MAP"]
        result = self.hypotheses.validate(unknown)
        self.assertFalse(result["valid"])
        self.assertTrue(any("unknown evidence IDs" in item["message"] for item in result["findings"]))

        from_map = self.hypothesis_payload()
        from_map.pop("known_evidence_ids")
        from_map["evidence_map"] = {
            "rows": [{"evidence_id": "E1", "record_id": "REC-1"}, {"evidence_id": "E2", "record_id": "REC-2"}],
            "claims": [],
            "gaps": [],
        }
        mapped = self.hypotheses.validate(from_map)
        self.assertTrue(mapped["valid"], mapped)
        self.assertGreaterEqual(mapped["known_evidence_count"], 2)


if __name__ == "__main__":
    unittest.main()
