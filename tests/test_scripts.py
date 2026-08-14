from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ScriptTests(unittest.TestCase):
    def test_literature_normalization_and_deduplication(self):
        module = load_module("normalize_records", "skills/zju-literature-search/scripts/normalize_records.py")
        records = [
            {"title": "A Study", "doi": "https://doi.org/10.1/ABC", "source": "Crossref", "query_id": "Q1"},
            {"title": "A study.", "DOI": "10.1/abc", "database": "OpenAlex", "query_id": "Q2"},
            {"title": "No identifier paper", "year": 2020, "source": "Index"},
        ]
        output = module.deduplicate(records)
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0]["source_database"], ["Crossref", "OpenAlex"])
        self.assertFalse(output[0]["identifier_missing"])
        self.assertTrue(output[1]["identifier_missing"])

        saturation = load_module("assess_search_saturation", "skills/zju-literature-search/scripts/assess_search_saturation.py")
        report = saturation.assess({
            "required_concepts": ["material", "failure"],
            "required_source_families": ["broad_index", "domain_database"],
            "rounds": [
                {"round_id": "R1", "strategy": "core_query", "source_family": "broad_index", "record_ids": ["A", "B"], "eligible_ids": ["A"], "concepts_covered": ["material"]},
                {"round_id": "R2", "strategy": "forward_chaining", "source_family": "domain_database", "record_ids": ["A"], "eligible_ids": [], "concepts_covered": ["failure"]},
                {"round_id": "R3", "strategy": "contradiction_query", "source_family": "broad_index", "record_ids": ["C"], "eligible_ids": ["C"], "concepts_covered": ["material", "failure"]},
            ],
            "evidence_gaps": [],
        })
        self.assertTrue(report["saturation"]["stop_recommended"], report)
        self.assertEqual(report["totals"]["unique_eligible"], 2)
        incomplete = saturation.assess({
            "required_concepts": ["material", "failure"],
            "rounds": [{"round_id": "R1", "record_ids": ["A"], "eligible_ids": ["A"], "concepts_covered": ["material"]}],
        })
        self.assertFalse(incomplete["saturation"]["stop_recommended"])
        self.assertEqual(incomplete["saturation"]["next_action"], "run_gap_targeted_query_for_missing_concepts")

    def test_literature_normalization_preserves_identity_free_rows(self):
        module = load_module("normalize_records_identity_gap", "skills/zju-literature-search/scripts/normalize_records.py")
        output = module.deduplicate([
            {"source": "manual export", "query_id": "Q1"},
            {"source": "manual export", "query_id": "Q1"},
        ])
        self.assertEqual(len(output), 2)
        self.assertEqual([row["source_record_positions"] for row in output], [[1], [2]])
        self.assertEqual(len({row["identity_key"] for row in output}), 2)
        self.assertTrue(all(row["identity_key"].startswith("anonymous:") for row in output))
        self.assertTrue(all(row["identity_missing"] for row in output))
        self.assertTrue(all(any("stable ID derives from normalized content" in warning for warning in row["warnings"]) for row in output))

    def test_fulltext_route_and_sensitive_input(self):
        module = load_module("classify_access", "skills/zju-fulltext-access/scripts/classify_access.py")
        self.assertEqual(module.classify({"oa_url": "https://example.org/a"})["route"], "open_access")
        result = module.classify({"database": "Scopus"})
        self.assertEqual(result["route"], "zju_library_interactive")
        self.assertTrue(result["requires_interactive_authentication"])
        blocked = module.classify({"doi": "10.1/a", "password": "do-not-store"})
        self.assertEqual(blocked["status"], "blocked_sensitive_input")
        source_pack = module.classify({
            "record_id": "REC-1", "doi": "https://doi.org/10.1/A", "oa_url": "https://example.org/paper.pdf",
            "supplement_url": "https://example.org/supp.pdf", "data_url": "https://example.org/data",
            "requested_components": ["full_text", "supplement", "data"], "version_requested": "version_of_record",
        })
        self.assertTrue(source_pack["source_package_complete"], source_pack)
        self.assertEqual(source_pack["reader_handoff"]["record_id"], "REC-1")
        self.assertTrue(source_pack["reader_handoff"]["ready"])
        self.assertEqual(source_pack["route_candidates"][0]["route"], "open_access")

    def test_reference_metadata_comparison(self):
        module = load_module("compare_metadata", "skills/zju-reference-audit/scripts/compare_metadata.py")
        doi_row = module.compare_field("doi", "https://doi.org/10.1/ABC.", "10.1/abc")
        self.assertEqual(doi_row["status"], "match")
        author_row = module.compare_field("authors", ["Li", "Wang"], ["Wang", "Li"])
        self.assertEqual(author_row["status"], "material_mismatch")

    def test_reference_fixture_meets_detection_and_false_positive_gates(self):
        comparator = load_module("compare_metadata_fixture", "skills/zju-reference-audit/scripts/compare_metadata.py")
        scorer = load_module("score_reference_fixture", "evals/score_reference_fixture.py")
        fixture = json.loads((ROOT / "evals/fixtures/reference-audit-30.json").read_text(encoding="utf-8"))
        predictions = [comparator.audit(item, index) for index, item in enumerate(fixture["records"], 1)]
        result = scorer.score(fixture, predictions)
        self.assertEqual(len(predictions), 30)
        self.assertTrue(result["passes_detection_gate"], result)
        self.assertTrue(result["passes_false_positive_gate"], result)

    def test_reader_structure_validation(self):
        module = load_module("validate_reader", "skills/zju-paper-reader/scripts/validate_reader.py")
        valid = """# Source identity\n# Coverage\n# Research question\n# Methods\n# Argument spine\n# Claim-evidence map\nclaim_id: REC-1-C1\n# Primary quantitative findings\n# Robustness, contradictions, and alternative explanations\n# Limitations\n# Evidence-synthesis handoff\n# Figure inventory\n# Table inventory\n# Equation inventory\n# Source anchors\n[p. 2, Results]\n"""
        result = module.validate(valid, "paper-card")
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["claim_rows_detected"], 1)
        self.assertFalse(module.validate("# Summary\nOnly an abstract", "paper-card")["valid"])

    def test_experiment_log_is_traceable_and_does_not_move_source(self):
        module = load_module("build_log", "skills/zju-experiment-log/scripts/build_log.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "observation.txt"
            source.write_text("raw", encoding="utf-8")
            content = module.render({"experiment_id": "EXP-1", "title": "Test", "source_files": [str(source)]})
            self.assertTrue(source.exists())
            self.assertIn("experiment_id: \"EXP-1\"", content)
            self.assertIn(str(source), content)
            self.assertIn("SHA-256", content)
            self.assertIn("sample_ids: []", content)
        with self.assertRaises(ValueError):
            module.render({"experiment_id": "EXP-2", "token": "sensitive"})

    def test_statistics_scanner_labels_findings_as_triage(self):
        module = load_module("scan_reporting", "skills/zju-statistics-audit/scripts/scan_reporting.py")
        result = module.scan("The result was significant, P < 0.05. n = 12.")
        self.assertEqual(result["status"], "triage_only")
        checks = {item["check"] for item in result["warnings"]}
        self.assertIn("effect_size", checks)
        self.assertIn("p_without_effect_size", checks)

        analysis = load_module("validate_analysis_contract", "skills/zju-statistics-audit/scripts/validate_analysis_contract.py")
        contract = {
            "contract_id": "AC-1", "study_id": "S-1", "question": "Does treatment change Y?", "design_stage": "frozen",
            "design": {"experimental_unit": "animal", "observational_unit": "measurement", "assignment": "randomized", "randomization_unit": "animal", "grouping_structure": "animal within batch", "repeated_measures": True},
            "outcomes": [{"outcome_id": "Y14", "role": "primary", "variable": "Y", "scale": "continuous", "timepoint": "day 14"}],
            "analyses": [{"analysis_id": "A1", "outcome_ids": ["Y14"], "estimand": "day-14 mean difference", "analysis_population": "all randomized animals", "model_family": "linear mixed model", "effect_measure": "mean difference", "uncertainty": "95% CI", "missing_data_strategy": "likelihood under MAR plus MNAR sensitivity", "multiplicity_family": "single primary contrast", "diagnostics": ["residual and influence checks"], "sensitivity_analyses": ["robust model"], "grouping_terms": ["animal", "batch"]}],
        }
        self.assertTrue(analysis.validate(contract)["plan_ready"])
        invalid_contract = {**contract, "analyses": [{**contract["analyses"][0], "grouping_terms": [], "sensitivity_analyses": []}]}
        self.assertFalse(analysis.validate(invalid_contract)["valid"])

        registry = load_module("reconcile_result_registry", "skills/zju-statistics-audit/scripts/reconcile_result_registry.py")
        payload = {
            "schema_version": "1.0", "study_id": "S-1", "analysis_contract_id": "AC-1", "registry_version": "1",
            "analysis_contract": contract,
            "results": [{"result_id": "RES-1", "analysis_id": "A1", "outcome_id": "Y14", "result_kind": "inferential", "analysis_population": "all randomized animals", "effect_measure": "mean difference", "estimate": -2.4, "unit": "mg/L", "direction": "lower_in_treatment", "ci": {"level": 0.95, "lower": -3.7, "upper": -1.1}, "p_value": 0.0012, "multiplicity_status": "single primary contrast", "n": {"experimental_units": 48, "observations": 48}, "diagnostics": [], "sensitivity_analyses": [], "source_anchor": "analysis.json#/primary", "status": "verified"}],
            "uses": [{"use_id": "U1", "consumer_type": "figure", "anchor": "Fig. 2a", "result_id": "RES-1", "values": {"estimate": -2.4, "ci.lower": -3.7, "n.experimental_units": 48}}],
        }
        self.assertTrue(registry.validate(payload)["valid"])
        renderer = load_module("render_result_tokens", "skills/zju-statistics-audit/scripts/render_result_tokens.py")
        rendered = renderer.render("Difference {{result:RES-1:estimate|.1f}} mg/L (n={{result:RES-1:n.experimental_units}}).", payload)
        self.assertTrue(rendered["valid"], rendered)
        self.assertEqual(rendered["text"], "Difference -2.4 mg/L (n=48).")
        self.assertEqual(len(rendered["uses"]), 2)
        payload["uses"][0]["values"]["estimate"] = 2.4
        self.assertFalse(registry.validate(payload)["valid"])

    def test_claim_ledger_rejects_unsupported_claims(self):
        module = load_module("check_claim_ledger", "skills/zju-scientific-writing/scripts/check_claim_ledger.py")
        invalid = module.validate([{"claim_id": "C1", "claim": "A", "claim_type": "literature", "status": "supported"}])
        self.assertFalse(invalid["valid"])
        valid = module.validate([{"claim_id": "C1", "claim": "A", "claim_type": "literature", "status": "supported", "evidence_ids": ["R1"], "source_anchor": "p. 2", "citation_verified": True}])
        self.assertTrue(valid["valid"])
        author_claim = [{"claim_id": "C2", "claim": "Treatment lowered Y", "claim_type": "author_result", "status": "supported", "evidence_ids": ["FIG-2A"], "result_ids": ["RES-1"], "source_anchor": "Results 2"}]
        self.assertTrue(module.validate(author_claim, known_result_ids={"RES-1"}, require_result_ids=True)["valid"])
        self.assertFalse(module.validate(author_claim, known_result_ids={"RES-2"}, require_result_ids=True)["valid"])

    def test_writing_contract_requires_results_and_reviewer_gate(self):
        module = load_module("check_writing_contract", "skills/zju-scientific-writing/scripts/check_writing_contract.py")
        base = {
            "contributions": [{
                "contribution_id": "K1",
                "statement": "A bounded contribution",
                "need": "A defined research gap",
                "evidence_ids": ["E1"],
                "claim_boundary": "Applies to the tested system only",
                "status": "confirmed",
            }],
            "results_sections": [{"section_id": "R1", "contribution_ids": ["K1"], "evidence_ids": ["E1"]}],
            "reviewer_objections": [{
                "objection_id": "O1",
                "risk": "External validity is bounded",
                "disposition": "accepted_limitation",
                "response": "Disclose the tested population and boundary.",
            }],
            "submission_ready": True,
        }
        self.assertTrue(module.validate(base)["valid"])
        v2 = {**base, "workflow_version": "2.0", "known_result_ids": ["RES-1"], "results_sections": [{"section_id": "R1", "contribution_ids": ["K1"], "evidence_ids": ["E1"], "result_ids": ["RES-1"]}]}
        self.assertTrue(module.validate(v2)["valid"])
        v2["results_sections"][0]["result_ids"] = ["UNKNOWN"]
        self.assertFalse(module.validate(v2)["valid"])
        invalid = dict(base)
        invalid["results_sections"] = []
        invalid["reviewer_objections"] = [{"objection_id": "O1", "risk": "Missing control", "disposition": "open"}]
        self.assertFalse(module.validate(invalid)["valid"])

    def test_run_manifest_requires_traceable_artifacts_and_decision_owner(self):
        module = load_module("validate_run_manifest", "skills/zju-experiment-log/scripts/validate_run_manifest.py")
        valid = module.validate({
            "run_id": "RUN-1",
            "experiment_id": "EXP-1",
            "started_at": "2026-08-10T08:00:00+08:00",
            "status": "completed",
            "inputs": [{"path": "raw/input.csv", "sha256": "a" * 64}],
            "parameters": {"temperature_C": 25},
            "software": [{"name": "instrument", "version": "1.0"}],
            "outputs": [{"path": "derived/result.csv", "sha256": "b" * 64}],
            "decision_gate": {"criterion": "QC within range", "decision": "continue", "actor": "operator", "decided_at": "2026-08-10T09:00:00+08:00"},
        })
        self.assertTrue(valid["valid"], valid)
        v2 = module.validate({
            "schema_version": "2.0", "run_type": "experiment", "run_id": "RUN-3", "experiment_id": "EXP-1", "started_at": "2026-08-10T08:00:00+08:00", "status": "completed",
            "inputs": [{"artifact_id": "RAW-1", "role": "raw_data", "path": "raw/input.csv", "sha256": "a" * 64}],
            "parameters": {}, "software": [{"name": "instrument", "version": "1.0"}],
            "outputs": [{"artifact_id": "OUT-1", "role": "analysis_output", "path": "derived/result.csv", "sha256": "b" * 64, "derived_from": ["RAW-1"]}],
            "design_snapshot": {"experimental_unit": "sample", "observational_unit": "measurement", "outcome_ids": ["Y1"]},
            "analysis_contract": {"contract_id": "AC-1", "status": "frozen", "path": "analysis-contract.json", "sha256": "c" * 64},
            "decision_gate": {"criterion": "QC within range", "decision": "continue", "actor": "operator", "decided_at": "2026-08-10T09:00:00+08:00"},
        })
        self.assertTrue(v2["valid"], v2)
        invalid = module.validate({
            "run_id": "RUN-2", "experiment_id": "EXP-1", "started_at": "unknown", "status": "completed",
            "inputs": [], "parameters": {}, "software": [], "outputs": [],
            "decision_gate": {"criterion": "QC", "decision": "continue"},
        })
        self.assertFalse(invalid["valid"])

    def test_expansion_discovery_and_design_validators(self):
        monitor = load_module("update_monitor_state", "skills/zju-literature-monitor/scripts/update_monitor_state.py")
        result = monitor.update(
            {
                "profile_id": "MON-1",
                "seen": [{
                    "identity": "arxiv:2401.12345",
                    "aliases": [],
                    "work_identity": "work:paper-1",
                    "first_seen_at": "2026-01-01",
                    "last_seen_at": "2026-01-01",
                    "current_state": {"version_status": "preprint"},
                    "history": [{"observed_at": "2026-01-01", "change_class": "new", "identity": "arxiv:2401.12345", "state": {"version_status": "preprint"}}],
                }],
            },
            [
                {"doi": "10.1/old", "title": "Old", "work_id": "paper-1", "version_status": "version of record"},
                {"doi": "https://doi.org/10.1/NEW", "title": "New"},
            ],
            "2026-08-11T09:00:00+08:00",
        )
        self.assertEqual(result["counts"]["new"], 1)
        self.assertEqual(result["counts"]["updated"], 1)
        updated = next(row for row in result["records"] if row["change_class"] == "updated")
        self.assertIn("linked_identity_changed", updated["state_transition"]["reason"])
        self.assertIn("version_status_changed", updated["state_transition"]["reason"])
        stored = next(row for row in result["state"]["seen"] if row["identity"] == "doi:10.1/old")
        self.assertIn("arxiv:2401.12345", stored["aliases"])
        self.assertEqual(stored["current_state"]["version_status"], "version_of_record")
        self.assertEqual(len(stored["history"]), 2)

        unchanged = monitor.update(
            result["state"],
            [{"doi": "10.1/old", "title": "Old", "work_id": "paper-1", "version_status": "version_of_record"}],
            "2026-08-18T09:00:00+08:00",
        )
        self.assertEqual(unchanged["counts"]["updated"], 0)
        self.assertEqual(unchanged["counts"]["duplicate"], 1)
        unchanged_stored = next(row for row in unchanged["state"]["seen"] if row["identity"] == "doi:10.1/old")
        self.assertEqual(len(unchanged_stored["history"]), 2)

        same_title_different_doi = monitor.update(
            {
                "profile_id": "MON-2",
                "seen": [{
                    "identity": "doi:10.1/first",
                    "work_identity": "title-year:shared title|2026",
                    "first_seen_at": "2026-08-01",
                    "current_state": {"version_status": "version_of_record"},
                }],
            },
            [{"doi": "10.1/second", "title": "Shared title", "year": 2026, "version_status": "version_of_record"}],
            "2026-08-18T09:00:00+08:00",
        )
        self.assertEqual(same_title_different_doi["counts"]["new"], 1)
        self.assertEqual(same_title_different_doi["counts"]["updated"], 0)
        with self.assertRaises(ValueError):
            monitor.update({"profile_id": "MON-1", "token": "secret"}, [], "2026-08-11")

        retraction = monitor.update(
            result["state"],
            [{"doi": "10.1/old", "work_id": "paper-1", "retraction_status": "retracted", "claim_ids": ["CLM-1"], "hypothesis_ids": ["H1"]}],
            "2026-08-25T09:00:00+08:00",
        )
        self.assertEqual(retraction["handoff_queue"][0]["urgency"], "critical")
        self.assertEqual(retraction["handoff_queue"][0]["linked_ids"]["claim_ids"], ["CLM-1"])
        self.assertIn("reopen_evidence_synthesis", retraction["handoff_queue"][0]["actions"])

        evidence = load_module("validate_evidence_table", "skills/zju-evidence-synthesis/scripts/validate_evidence_table.py")
        row = {
            "record_id": "REC-1", "study_id": "S1", "report_id": "R1", "citation_id": "10.1/x", "design": "RCT",
            "population_or_system": "adults", "sample_and_unit": "40 participants",
            "intervention_or_exposure": "A", "comparator": "B", "outcome": "Y", "time_point": "week 4",
            "effect_estimate": 1.2, "uncertainty": "95% CI 1.0 to 1.4", "risk_of_bias": "low",
            "source_anchor": "Table 2",
        }
        self.assertTrue(evidence.validate([row])["valid"])
        self.assertFalse(evidence.validate([{**row, "uncertainty": None}])["valid"])
        conflict_builder = load_module("build_conflict_matrix", "skills/zju-evidence-synthesis/scripts/build_conflict_matrix.py")
        evidence_bundle = {
            "rows": [
                {**row, "claim_id": "CLM-1", "evidence_role": "supports", "directness": "direct", "result_direction": "benefit", "measurement_method": "assay-A"},
                {**row, "record_id": "REC-2", "study_id": "S2", "report_id": "R2", "citation_id": "10.1/y", "claim_id": "CLM-1", "evidence_role": "contradicts", "directness": "direct", "result_direction": "harm", "measurement_method": "assay-B"},
            ],
            "claims": [{"claim_id": "CLM-1", "certainty": "low", "supporting_study_ids": ["S1"], "contradicting_study_ids": ["S2"], "contextual_study_ids": []}],
            "conflicts": [{"conflict_id": "CF-1", "claim_id": "CLM-1", "study_ids": ["S1", "S2"]}],
        }
        self.assertTrue(evidence.validate(evidence_bundle)["valid"], evidence.validate(evidence_bundle))
        conflict = conflict_builder.build(evidence_bundle["rows"])
        self.assertEqual(conflict["summary"]["directional_conflicts"], 1)
        self.assertEqual(conflict["groups"][0]["resolution_state"], "context_hypothesis_available")
        no_conflict_record = dict(evidence_bundle)
        no_conflict_record["conflicts"] = []
        self.assertFalse(evidence.validate(no_conflict_record)["valid"])

        hypotheses = load_module("validate_hypothesis_set", "skills/zju-hypothesis-design/scripts/validate_hypothesis_set.py")
        plan = {
            "hypotheses": [
                {"hypothesis_id": "H1", "mechanism": "M1", "assumptions": ["A"], "evidence_ids": ["E1"], "unique_predictions": ["P1"], "falsifiers": ["F1"], "alternative_ids": ["H2"]},
                {"hypothesis_id": "H2", "mechanism": "M2", "assumptions": ["B"], "evidence_ids": ["E1"], "unique_predictions": ["P2"], "falsifiers": ["F2"], "alternative_ids": ["H1"]},
            ],
            "experiments": [{"experiment_id": "X1", "hypothesis_ids": ["H1", "H2"], "experimental_unit": "sample", "intervention": "perturb", "control": "vehicle", "primary_outcome": "signal", "decision_rule": "predefined contrast"}],
        }
        self.assertTrue(hypotheses.validate(plan)["valid"])
        self.assertFalse(hypotheses.validate({"hypotheses": plan["hypotheses"][:1], "experiments": []})["valid"])
        plan_v2 = {
            "schema_version": "2.0",
            "known_evidence_ids": ["E1", "E2", "E3"],
            "hypotheses": [
                {**plan["hypotheses"][0], "boundary_conditions": ["25 C"], "contradicting_evidence_ids": ["E2"]},
                {**plan["hypotheses"][1], "boundary_conditions": ["25 C"], "contradicting_evidence_ids": ["E3"]},
            ],
            "experiments": [
                {**plan["experiments"][0], "experiment_id": "X-fast", "predicted_outcomes": {"H1": "increase", "H2": "no_change"}, "inconclusive_region": "effect within assay noise", "feasibility": {"technical": 0.9, "sample_access": 0.8}, "cost_level": 1, "time_level": 1, "orthogonal_measurement": True},
                {**plan["experiments"][0], "experiment_id": "X-weak", "predicted_outcomes": {"H1": "increase", "H2": "increase"}, "inconclusive_region": "effect within assay noise", "feasibility": {"technical": 1.0}, "cost_level": 1, "time_level": 1, "orthogonal_measurement": False},
            ],
        }
        ranked = hypotheses.validate(plan_v2)
        self.assertTrue(ranked["valid"], ranked)
        self.assertEqual(ranked["experiment_discrimination_ranking"][0]["experiment_id"], "X-fast")
        self.assertEqual(ranked["experiment_discrimination_ranking"][0]["pair_discrimination_coverage"], 1.0)
        self.assertIn("not probability of truth", ranked["ranking_note"])

        figure = load_module("validate_figure_manifest", "skills/zju-scientific-figure/scripts/validate_figure_manifest.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            source = base_dir / "data.csv"
            source.write_bytes(b"x,y\n1,2\n")
            export = base_dir / "figure.svg"
            export.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
            figure_plan = {
                "figure_id": "F1", "bounded_conclusion": "A increases Y in the tested system", "route": "data_figure",
                "backend": "python", "source_files": [{"path": "data.csv", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
                "panels": [{"panel_id": "A", "question": "Does A alter Y?", "source_anchor": "data.csv#columns=x,y", "panel_type": "scatter", "generated": False}],
                "exports": ["figure.svg"],
            }
            result = figure.validate(figure_plan, base_dir=base_dir)
            self.assertTrue(result["valid"], result)
            self.assertEqual(result["verified_sources"], 1)
            self.assertEqual(result["verified_exports"], 1)
            self.assertEqual(result["semantic_inspection"], "not_performed")
            self.assertFalse(result["publication_ready"])

            generated = json.loads(json.dumps(figure_plan))
            generated["panels"][0]["generated"] = True
            self.assertFalse(figure.validate(generated, base_dir=base_dir)["valid"])

            placeholder = json.loads(json.dumps(figure_plan))
            placeholder["panels"][0]["source_anchor"] = "SOURCE_REQUIRED"
            self.assertFalse(figure.validate(placeholder, base_dir=base_dir)["valid"])

            missing_sources = json.loads(json.dumps(figure_plan))
            missing_sources["source_files"] = []
            self.assertFalse(figure.validate(missing_sources, base_dir=base_dir)["valid"])

            source.write_bytes(b"x,y\n1,999\n")
            self.assertFalse(figure.validate(figure_plan, base_dir=base_dir)["valid"])

            source.write_bytes(b"x,y\n1,2\n")
            export.write_bytes(b"this is not SVG")
            self.assertFalse(figure.validate(figure_plan, base_dir=base_dir)["valid"])

            export.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
            unsupported = base_dir / "figure.unknown"
            unsupported.write_bytes(b"unrecognized deterministic export format")
            partially_verified = json.loads(json.dumps(figure_plan))
            partially_verified["exports"] = ["figure.svg", "figure.unknown"]
            unsupported_result = figure.validate(partially_verified, base_dir=base_dir)
            self.assertFalse(unsupported_result["valid"], unsupported_result)
            self.assertEqual(unsupported_result["verified_exports"], 1)
            self.assertFalse(unsupported_result["deterministic_exports_complete"])
            self.assertTrue(any(
                item["severity"] == "error"
                and item["field"] == "exports[2].format"
                and "no deterministic signature check" in item["message"]
                for item in unsupported_result["findings"]
            ))

    def test_expansion_communication_and_sharing_validators(self):
        deck = load_module("validate_deck_plan", "skills/zju-paper2ppt/scripts/validate_deck_plan.py")
        deck_plan = {
            "project_id": "D1", "source_id": "doi:10.1/x", "paper_type": "discovery", "audience": "group meeting",
            "duration_minutes": 10, "terminology_ledger": {"ABC": "term"},
            "slides": [{"slide_id": "S1", "title": "Question", "claim": "The paper tests X", "source_anchors": ["p.1"], "speaker_notes": "Introduce the question", "estimated_seconds": 60}],
        }
        self.assertTrue(deck.validate(deck_plan)["valid"])
        self.assertFalse(deck.validate({**deck_plan, "duration_minutes": 0.5})["valid"])

        reviewer = load_module("validate_review_report", "skills/zju-reviewer/scripts/validate_review_report.py")
        report = {
            "mode": "single_review", "assessment_boundary": "Methods and Results only",
            "concerns": [{"concern_id": "R1-M1", "severity": "major", "blocking": True, "axis": "validity", "claim_pointer": "Results 1", "evidence_pointer": "Fig. 1", "concern": "Control missing", "why_it_matters": "Inference is ambiguous", "resolution_test": "Add or justify control"}],
        }
        self.assertTrue(reviewer.validate(report)["valid"])
        report["concerns"][0].update({"severity": "minor", "blocking": True})
        self.assertFalse(reviewer.validate(report)["valid"])
        report_v2 = {
            "workflow_version": "2.0", "mode": "single_review", "assessment_boundary": "full manuscript",
            "concerns": [{"concern_id": "R1-M1", "severity": "major", "blocking": False, "axis": "robustness", "claim_pointer": "Results 1", "evidence_pointer": "RES-1", "claim_ids": ["C1"], "evidence_ids": ["AN-1"], "result_ids": ["RES-1"], "concern": "Sensitivity analysis is absent", "why_it_matters": "The conclusion depends on a model assumption", "requested_evidence": ["prespecified robustness analysis"], "action_options": ["run sensitivity analysis", "bound the claim"], "resolution_test": "Report the sensitivity estimate and whether direction and material magnitude persist"}],
        }
        self.assertTrue(reviewer.validate(report_v2)["response_handoff_ready"])

        response = load_module("validate_response_tracker", "skills/zju-review-response/scripts/validate_response_tracker.py")
        tracker = {"items": [{"comment_id": "R1.1", "source_role": "reviewer_1", "verbatim_comment": "Add control", "action_type": "manuscript_edit", "requested_action": "Clarify control", "evidence_status": "verified", "status": "verified_complete", "response_text": "We clarified it", "manuscript_change": "Added control definition", "location": "Methods, paragraph 2"}]}
        self.assertTrue(response.validate(tracker)["ready"])
        tracker["items"][0]["location"] = "LOCATION_PENDING"
        self.assertFalse(response.validate(tracker)["valid"])
        tracker_v2 = {"workflow_version": "2.0", "items": [{"comment_id": "R1.1", "concern_id": "R1-M1", "action_id": "ACT-1", "source_role": "reviewer_1", "verbatim_comment": "Run a robustness analysis", "action_type": "new_analysis", "requested_action": "Test robustness", "evidence_status": "verified", "evidence_ids": ["AN-ROBUST"], "result_ids": ["RES-ROBUST"], "status": "verified_complete", "response_text": "We added the analysis.", "manuscript_change": "Added robustness result", "manuscript_diff": {"before": "No sensitivity result.", "after": "The sensitivity result was consistent.", "dependent_artifacts": ["Abstract", "Fig. 2", "Supplement"]}, "result_reconciliation": {"status": "passed", "registry_version": "2"}, "location": "Results, paragraph 3"}]}
        self.assertTrue(response.validate(tracker_v2)["ready"])

        availability = load_module("validate_data_inventory", "skills/zju-data-availability/scripts/validate_data_inventory.py")
        inventory = {"artifacts": [{"artifact_id": "D1", "description": "source data", "supports_claims": ["C1"], "controller": "authors", "access_route": "public_repository", "status": "ready", "repository": "Example Repository", "identifier": "doi:10.1/data"}]}
        self.assertTrue(availability.validate(inventory)["valid"])
        del inventory["artifacts"][0]["identifier"]
        self.assertFalse(availability.validate(inventory)["valid"])
        inventory_v2 = {
            "workflow_version": "2.0", "computational_results_present": True,
            "artifacts": [
                {"artifact_id": "D1", "artifact_class": "analysis_code", "description": "analysis code", "supports_claims": ["C1"], "supports_results": ["RES-1"], "derived_from": [], "controller": "authors", "access_route": "public_repository", "status": "ready", "repository": "Example Repository", "identifier": "doi:10.1/code", "version": "1.0", "format": "Python"},
                {"artifact_id": "ENV1", "artifact_class": "environment", "description": "environment lock", "supports_claims": ["C1"], "supports_results": ["RES-1"], "derived_from": [], "controller": "authors", "access_route": "public_repository", "status": "ready", "repository": "Example Repository", "identifier": "doi:10.1/env", "version": "1.0", "format": "lockfile"},
            ],
            "reproducibility_packages": [{"package_id": "RP-1", "result_ids": ["RES-1"], "artifact_ids": ["D1", "ENV1"], "environment_artifact_id": "ENV1", "entrypoint": "python analysis.py", "expected_outputs": ["RES-1"], "verification": {"status": "passed"}}],
        }
        self.assertTrue(availability.validate(inventory_v2)["valid"])

    def test_expansion_proposal_patent_chemistry_and_integrity_validators(self):
        proposal = load_module("validate_proposal_manifest", "skills/zju-proposal-writer/scripts/validate_proposal_manifest.py")
        proposal_plan = {
            "proposal_id": "P1", "mode": "compose", "scheme_status": "official_verified",
            "objectives": [{"objective_id": "O1", "question": "Does X affect Y?", "success_criteria": ["estimate obtained"], "evidence_ids": ["E1"]}],
            "work_packages": [{"work_package_id": "WP1", "objective_ids": ["O1"], "methods": ["experiment"], "outputs": ["dataset"], "milestones": ["M1"], "decision_gate": "quality threshold"}],
            "evidence": [{"evidence_id": "E1"}], "risks": [{"risk_id": "R1"}], "compliance": [{"item": "ethics"}], "submission_ready": True,
        }
        self.assertTrue(proposal.validate(proposal_plan)["submission_ready"])
        proposal_plan["scheme_status"] = "template_pending"
        self.assertFalse(proposal.validate(proposal_plan)["valid"])

        proposal_handoff = {
            "proposal_id": "P2", "mode": "compose", "scheme_status": "official_verified",
            "evidence": [
                {"evidence_id": "EV-1", "status": "verified", "source_anchor": "doi:10.1000/example#fig-2"},
                {"evidence_id": "EV-2", "status": "verified", "source_anchor": "run:R-1#result-1"},
            ],
            "hypotheses": [
                {"hypothesis_id": "H0", "statement": "The intervention does not change Y", "evidence_ids": ["EV-1"], "predictions": ["No material contrast"], "falsifiers": ["A replicated material contrast"]},
                {"hypothesis_id": "H1", "statement": "The intervention changes Y through M", "evidence_ids": ["EV-1", "EV-2"], "predictions": ["Y and M change together"], "falsifiers": ["Y changes without M"]},
            ],
            "objectives": [{"objective_id": "O1", "question": "Which hypothesis explains Y?", "hypothesis_ids": ["H0", "H1"], "evidence_ids": ["EV-1"], "success_criteria": ["Resolve the prespecified decision rule"]}],
            "experiments": [{
                "experiment_id": "EX1", "objective_ids": ["O1"], "hypothesis_ids": ["H0", "H1"], "evidence_ids": ["EV-1", "EV-2"],
                "inputs": ["qualified samples"], "experimental_unit": "sample", "methods": ["controlled perturbation"], "outputs": ["effect estimate", "mechanism readout"],
                "capability_ids": ["CAP-1"], "depends_on_experiment_ids": [], "owner": "WP lead", "start_month": 1, "end_month": 6,
                "milestone_id": "M1", "milestone_acceptance": "QC passes and both outcomes are estimable",
                "decision_rule": {"metric": "joint Y/M pattern", "branches": {
                    "success": {"condition": "pattern matches H1", "next": "advance"},
                    "inconclusive": {"condition": "precision target is not met", "next": "repeat_or_redesign"},
                    "failure": {"condition": "pattern contradicts H1", "next": "pivot_to_H0"},
                }},
            }],
            "capabilities": [{"capability_id": "CAP-1", "item": "validated assay", "dimension": "method", "status": "available", "evidence_ids": ["EV-2"], "constraint": "", "mitigation": ""}],
            "risks": [{"risk_id": "R1"}], "compliance": [{"item": "call checked"}], "submission_ready": True,
        }
        compiled = proposal.compile_handoff(proposal_handoff)
        self.assertTrue(compiled["submission_ready"], compiled)
        self.assertEqual(compiled["work_package_sequence"], ["WP-EX1"])
        self.assertEqual(compiled["milestone_decision_tree"][0]["branches"]["inconclusive"]["next"], "repeat_or_redesign")
        self.assertEqual(compiled["feasibility_matrix"][0]["status"], "available")
        broken_proposal = json.loads(json.dumps(compiled["manifest"]))
        del broken_proposal["work_packages"][0]["decision_gate"]["branches"]["inconclusive"]
        self.assertFalse(proposal.validate(broken_proposal)["execution_plan_ready"])

        patent = load_module("validate_feature_ledger", "skills/zju-paper-to-patent/scripts/validate_feature_ledger.py")
        ledger = {
            "sources": [{"source_id": "P1", "source_type": "paper", "path": "source.pdf"}],
            "features": [{
                "feature_id": "FT-1", "normalized_term": "controller", "description": "controls output",
                "source_ids": ["P1"], "source_anchor": "p. 4, lines 10-12", "support_state": "explicit",
                "claim_role": "independent", "confidentiality": "unpublished",
            }],
        }
        self.assertTrue(patent.validate(ledger)["valid"])
        ledger["features"][0]["support_state"] = "unsupported"
        self.assertFalse(patent.validate(ledger)["valid"])

        invention_packet = {
            "workflow_version": "2.0",
            "sources": [
                {"source_id": "P1", "source_type": "paper", "path": "paper.pdf"},
                {"source_id": "X1", "source_type": "experiment", "path": "experiment.md"},
            ],
            "evidence": [
                {"evidence_id": "EV-EFFECT", "source_id": "P1"},
                {"evidence_id": "EV-DISCRIM", "source_id": "X1"},
            ],
            "features": [
                {"feature_id": "FT-1", "normalized_term": "barrier layer", "description": "a barrier layer between substrate and active layer", "source_ids": ["P1"], "source_anchor": "p. 4, lines 10-12", "support_state": "explicit", "claim_role": "independent", "confidentiality": "public"},
                {"feature_id": "FT-2", "normalized_term": "fluorinated polymer", "description": "the barrier layer comprises a fluorinated polymer", "source_ids": ["P1"], "source_anchor": "p. 4, lines 10-12", "support_state": "explicit", "claim_role": "dependent", "confidentiality": "public"},
                {"feature_id": "FT-3", "normalized_term": "fluorinated copolymer", "description": "the barrier layer comprises a fluorinated copolymer", "source_ids": ["X1"], "source_anchor": "experiment X1, record 3", "support_state": "explicit", "claim_role": "embodiment", "confidentiality": "unpublished"},
            ],
            "technical_concepts": [{"concept_id": "IC-1", "problem": "moisture-driven interface degradation", "solution_feature_ids": ["FT-1", "FT-2"], "technical_effect": "retains device performance under the tested humidity protocol", "evidence_ids": ["EV-EFFECT"], "discriminating_evidence_ids": ["EV-DISCRIM"]}],
            "claim_candidates": [
                {"claim_id": "CL-1", "claim_role": "independent", "parent_claim_ids": [], "concept_ids": ["IC-1"], "feature_ids": ["FT-1"]},
                {"claim_id": "CL-2", "claim_role": "dependent", "parent_claim_ids": ["CL-1"], "concept_ids": ["IC-1"], "feature_ids": ["FT-2"]},
            ],
            "prior_art_terms": [{"query_id": "Q-1", "concept_ids": ["IC-1"], "target_feature_ids": ["FT-1", "FT-2"], "problem_terms": ["interface degradation"], "solution_terms": ["fluorinated polymer", "barrier layer"], "effect_terms": ["moisture stability"], "synonyms": ["humidity durability"], "classification_hints": ["classification pending"], "date_cutoff": "2026-08-11", "target_sources": ["patent database", "literature database"]}],
            "alternative_embodiments": [{"embodiment_id": "EB-1", "concept_id": "IC-1", "replaces_feature_ids": ["FT-2"], "alternative_feature_ids": ["FT-3"], "target_effect": "interface moisture barrier", "support_state": "explicit", "evidence_ids": ["EV-DISCRIM"], "discriminating_evidence_ids": ["EV-DISCRIM"]}],
        }
        invention_report = patent.validate(invention_packet)
        self.assertTrue(invention_report["invention_map_ready"], invention_report)
        self.assertEqual(invention_report["claim_dependency_order"], ["CL-1", "CL-2"])
        self.assertIn('"fluorinated polymer"', invention_report["prior_art_query_map"][0]["boolean_query"])
        self.assertTrue(invention_report["alternative_embodiment_map"][0]["discrimination_ready"])
        broken_dependency = json.loads(json.dumps(invention_packet))
        broken_dependency["claim_candidates"][1]["parent_claim_ids"] = ["CL-404"]
        self.assertFalse(patent.validate(broken_dependency)["invention_map_ready"])

    def test_patent_formal_claims_require_declared_anchored_numeric_support(self):
        patent = load_module("validate_feature_ledger_traceability", "skills/zju-paper-to-patent/scripts/validate_feature_ledger.py")
        feature = {
            "feature_id": "FT-2",
            "normalized_term": "heating step",
            "description": "heat the sample to 500 °C",
            "source_ids": ["P1"],
            "source_anchor": "p. 7, paragraph 2",
            "support_state": "explicit",
            "claim_role": "dependent",
            "confidentiality": "public",
            "numeric_invariant_checks": [{
                "literal": "500",
                "source_id": "P1",
                "source_anchor": "p. 7, paragraph 2",
                "source_value": "500 °C",
                "copied_value": "500 °C",
                "status": "match",
            }],
        }
        valid = {"sources": [{"source_id": "P1"}], "features": [feature]}
        self.assertTrue(patent.validate(valid)["valid"], patent.validate(valid))

        unknown_source = json.loads(json.dumps(valid))
        unknown_source["features"][0]["source_ids"] = ["P404"]
        unknown_source["features"][0]["numeric_invariant_checks"][0]["source_id"] = "P404"
        self.assertFalse(patent.validate(unknown_source)["valid"])

        missing_anchor = json.loads(json.dumps(valid))
        missing_anchor["features"][0]["source_anchor"] = "SOURCE_REQUIRED"
        self.assertFalse(patent.validate(missing_anchor)["valid"])

        unchecked_number = json.loads(json.dumps(valid))
        unchecked_number["features"][0]["numeric_invariant_checks"] = []
        self.assertFalse(patent.validate(unchecked_number)["valid"])

        altered_number = json.loads(json.dumps(valid))
        altered_number["features"][0]["numeric_invariant_checks"][0]["copied_value"] = "50 °C"
        self.assertFalse(patent.validate(altered_number)["valid"])

        chemistry = load_module("validate_chemistry_records", "skills/zju-chemistry-databases/scripts/validate_chemistry_records.py")
        chemistry_data = {
            "entities": [{"entity_id": "M1", "inchi_key": "ABCDEFGHIJKLMN-ABCDEFGHIJ-A", "identity_status": "resolved"}],
            "records": [{"record_id": "R1", "entity_id": "M1", "property_or_endpoint": "melting point", "value": 100, "unit": "degC", "evidence_type": "experimental", "source_database": "primary literature", "source_anchor": "Table 1"}],
        }
        self.assertTrue(chemistry.validate(chemistry_data)["valid"])
        matrix = load_module(
            "build_chemistry_evidence_matrix",
            "skills/zju-chemistry-databases/scripts/build_evidence_matrix.py",
        )
        matrix_payload = json.loads(json.dumps(chemistry_data))
        matrix_payload["records"][0].update({
            "conditions": {"pressure": "1 atm"},
            "method_or_assay": "capillary",
        })
        matrix_payload["records"].extend([
            {
                **matrix_payload["records"][0],
                "record_id": "R2",
                "value": 101,
                "source_anchor": "Table 2",
            },
            {
                **matrix_payload["records"][0],
                "record_id": "R3",
                "value": 99,
                "evidence_type": "predicted",
                "source_database": "model output",
                "source_anchor": "prediction run 1",
            },
        ])
        matrix_result = matrix.build(matrix_payload)
        self.assertTrue(matrix_result["valid"], matrix_result)
        self.assertEqual(len(matrix_result["comparison_groups"]), 2)
        measured_group = next(
            group for group in matrix_result["comparison_groups"] if group["record_count"] == 2
        )
        self.assertTrue(measured_group["heterogeneous_reported_values"])
        chemistry_data["records"][0]["entity_id"] = "missing"
        self.assertFalse(chemistry.validate(chemistry_data)["valid"])

        integrity = load_module("audit_provenance_manifest", "skills/zju-research-integrity/scripts/audit_provenance_manifest.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            artifact = base_dir / "raw.csv"
            artifact.write_bytes(b"sample,value\nA,1\n")
            manifest = {"artifacts": [{"artifact_id": "A1", "path": "raw.csv", "role": "raw", "created_at": "2026-08-11T09:00:00+08:00", "custodian": "researcher", "preservation_status": "immutable_original", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}], "links": []}
            result = integrity.validate(manifest, base_dir=base_dir)
            self.assertTrue(result["valid"], result)
            self.assertEqual(result["verified_artifacts"], 1)
            self.assertEqual(result["base_dir"], str(base_dir.resolve()))

            other_dir = base_dir / "elsewhere"
            other_dir.mkdir()
            self.assertFalse(integrity.validate(manifest, base_dir=other_dir)["valid"])

            artifact.write_bytes(b"sample,value\nA,2\n")
            self.assertFalse(integrity.validate(manifest, base_dir=base_dir)["valid"])

            artifact.write_bytes(b"sample,value\nA,1\n")
            changed_status = json.loads(json.dumps(manifest))
            changed_status["artifacts"][0]["preservation_status"] = "working_copy"
            self.assertFalse(integrity.validate(changed_status, base_dir=base_dir)["valid"])

    def test_reference_status_freshness_distinguishes_stale_cache(self):
        module = load_module("validate_status_freshness", "skills/zju-reference-audit/scripts/validate_status_freshness.py")
        records = [{"audit_id": "R1", "status_checked_at": "2026-08-01", "status_sources": ["publisher"], "version_status": "version_of_record"}]
        self.assertTrue(module.validate(records, date(2026, 8, 10), 90)["valid"])
        stale = module.validate(records, date(2027, 1, 1), 90)
        self.assertFalse(stale["valid"])
        self.assertEqual(stale["records"][0]["freshness"], "stale")

    def test_artifact_validator_clis_honor_explicit_base_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = root / "manifests"
            artifacts = root / "artifacts"
            manifests.mkdir()
            artifacts.mkdir()

            source = artifacts / "data.csv"
            source.write_bytes(b"x,y\n1,2\n")
            export = artifacts / "figure.svg"
            export.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
            figure_manifest = manifests / "figure.json"
            figure_manifest.write_text(json.dumps({
                "figure_id": "F1",
                "bounded_conclusion": "A is associated with Y in the supplied data",
                "route": "data_figure",
                "backend": "python",
                "source_files": [{"path": "data.csv", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
                "panels": [{"panel_id": "A", "question": "Is A associated with Y?", "source_anchor": "data.csv#columns=x,y", "panel_type": "scatter", "generated": False}],
                "exports": ["figure.svg"],
            }), encoding="utf-8")
            figure_command = [
                sys.executable,
                str(ROOT / "skills/zju-scientific-figure/scripts/validate_figure_manifest.py"),
                str(figure_manifest),
            ]
            without_base = subprocess.run(figure_command, text=True, capture_output=True, check=False)
            self.assertNotEqual(without_base.returncode, 0)
            with_base = subprocess.run(
                [*figure_command, "--base-dir", str(artifacts)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(with_base.returncode, 0, with_base.stdout + with_base.stderr)

            integrity_manifest = manifests / "integrity.json"
            integrity_manifest.write_text(json.dumps({
                "artifacts": [{
                    "artifact_id": "A1",
                    "path": "data.csv",
                    "role": "raw",
                    "created_at": "2026-08-11T09:00:00+08:00",
                    "custodian": "researcher",
                    "preservation_status": "immutable_original",
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }],
                "links": [],
            }), encoding="utf-8")
            integrity_command = [
                sys.executable,
                str(ROOT / "skills/zju-research-integrity/scripts/audit_provenance_manifest.py"),
                str(integrity_manifest),
            ]
            without_base = subprocess.run(integrity_command, text=True, capture_output=True, check=False)
            self.assertNotEqual(without_base.returncode, 0)
            with_base = subprocess.run(
                [*integrity_command, "--base-dir", str(artifacts)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(with_base.returncode, 0, with_base.stdout + with_base.stderr)

    def test_eval_suite_has_ten_cases_per_skill(self):
        data = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))
        counts = Counter(case["skill"] for case in data["cases"])
        self.assertEqual(len(data["cases"]), 70)
        self.assertEqual(len(counts), 7)
        self.assertTrue(all(count == 10 for count in counts.values()))

    def test_crosscutting_stress_suite_covers_every_first_wave_skill(self):
        data = json.loads((ROOT / "evals/stress-cases.json").read_text(encoding="utf-8"))
        skills = {case["skill"] for case in data["cases"]}
        self.assertEqual(len(data["cases"]), 7)
        self.assertEqual(skills, {
            "zju-literature-search", "zju-fulltext-access", "zju-reference-audit",
            "zju-paper-reader", "zju-experiment-log", "zju-statistics-audit",
            "zju-scientific-writing",
        })
        self.assertTrue(all(len(case.get("gold_checks", [])) >= 4 for case in data["cases"]))

    def test_expansion_suite_has_ten_cases_per_skill(self):
        data = json.loads((ROOT / "evals/expansion-cases.json").read_text(encoding="utf-8"))
        counts = Counter(case["skill"] for case in data["cases"])
        self.assertEqual(len(data["cases"]), 120)
        self.assertEqual(len(counts), 12)
        self.assertTrue(all(count == 10 for count in counts.values()))
        result = subprocess.run(
            [sys.executable, str(ROOT / "evals/validate_suite.py"), "--scope", "expansion", "--cases", str(ROOT / "evals/expansion-cases.json")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_expansion_evaluator_scoring_and_thresholds(self):
        module = load_module("expansion_eval", "evals/expansion_eval.py")
        perfect = {
            "critical_failure": False,
            "scores": {name: 4 for name in module.WEIGHTS},
        }
        self.assertEqual(module.primary_score(perfect), 100.0)
        perfect["critical_failure"] = True
        self.assertEqual(module.primary_score(perfect), 0.0)
        config = json.loads((ROOT / "evals/expansion-experiment.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(config["release_thresholds"]["minimum_overall_score"], 75.0)
        self.assertEqual(config["release_thresholds"]["maximum_critical_failures"], 0)
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = Path(temp_dir) / "fixture.json"
            module.write_fixture(fixture_path, {"text": "500小时"})
            self.assertTrue(fixture_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(json.loads(fixture_path.read_text(encoding="utf-8-sig"))["text"], "500小时")
        selected = module.selected_cases([], ["zju-paper2ppt", "zju-proposal-writer"])
        self.assertEqual(len(selected), 20)
        self.assertEqual({case["skill"] for case in selected}, {"zju-paper2ppt", "zju-proposal-writer"})

    def test_instruction_audit_accepts_combined_discovery_and_read_command(self):
        module = load_module("blind_eval_combined_read", "evals/blind_eval.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            skill = task_dir / "work/instructions/set-01/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")
            event = {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "status": "completed",
                    "exit_code": 0,
                    "command": "rg --files instructions; Get-Content -Raw instructions/set-01/SKILL.md",
                },
            }
            audit = module.instruction_read_audit(task_dir, json.dumps(event))
            self.assertTrue(audit["verified"], audit)

    def test_expansion_high_risk_invariants_are_explicit(self):
        patent = (ROOT / "skills/zju-paper-to-patent/SKILL.md").read_text(encoding="utf-8")
        chemistry = (ROOT / "skills/zju-chemistry-databases/SKILL.md").read_text(encoding="utf-8")
        proposal = (ROOT / "skills/zju-proposal-writer/SKILL.md").read_text(encoding="utf-8")
        response = (ROOT / "skills/zju-review-response/SKILL.md").read_text(encoding="utf-8")
        monitor = (ROOT / "skills/zju-literature-monitor/SKILL.md").read_text(encoding="utf-8")
        paper2ppt = (ROOT / "skills/zju-paper2ppt/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("digit-by-digit invariant check", patent)
        self.assertIn("blocking integrity failure", patent)
        self.assertIn("prose-only list", patent)
        self.assertIn("Authorship is never inventorship evidence", patent)
        self.assertIn("Never collapse an identity task to a one-line yes/no answer", chemistry)
        self.assertIn("salt/stereochemistry/isotope/charge assessment", chemistry)
        self.assertIn("Reject unsupported superlatives", proposal)
        self.assertIn("explicitly refuse to write it as completed", response)
        self.assertIn("must never be replaced by zero", monitor)
        self.assertIn("screening_accounting", monitor)
        self.assertIn("failed source retains prior state", monitor)
        self.assertIn("equation-selection table", paper2ppt)
        self.assertIn("fix-render-reinspect loop", paper2ppt)

    def test_expansion_safety_amendment_is_auditable(self):
        amendment = json.loads((ROOT / "evals/fixtures/expansion-safety-amendment-hyp-09.json").read_text(encoding="utf-8"))
        self.assertEqual(amendment["case_id"], "HYP-09")
        self.assertEqual(amendment["original_error_class"], "provider_safety_filter")
        self.assertNotEqual(amendment["base_prompt_sha256"], amendment["replacement_prompt_sha256"])
        self.assertEqual(len(amendment["unchanged_skill_instruction_sha256"]), 64)

    def test_portfolio_manifest_lists_twenty_skills(self):
        listed = {path.name for path in (ROOT / "skills").iterdir() if path.is_dir()}
        self.assertEqual(len(listed), 20)
        manifest = (ROOT / "provenance/portfolio-manifest.yaml").read_text(encoding="utf-8")
        self.assertIn("total_skills: 20", manifest)
        for skill in listed:
            self.assertIn(f"- {skill}", manifest)

    def test_noncommercial_sources_are_inspected_only(self):
        provenance = (ROOT / "provenance/sources.yaml").read_text(encoding="utf-8")
        matrix = (ROOT / "provenance/capability-matrix.yaml").read_text(encoding="utf-8")
        for source in ("academic-research-skills", "supervisor-skills"):
            self.assertIn(source, provenance)
            self.assertIn(source, matrix)
        self.assertIn("inspected_only_sources:", provenance)
        self.assertIn("adopted_into: []", matrix)

    def test_skill_metadata_and_progressive_disclosure(self):
        for skill_dir in (ROOT / "skills").iterdir():
            if not skill_dir.is_dir():
                continue
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            frontmatter = content.split("---", 2)[1]
            keys = [line.split(":", 1)[0].strip() for line in frontmatter.splitlines() if ":" in line]
            self.assertEqual(keys, ["name", "description"], skill_dir.name)
            self.assertNotIn("TODO", content)
            self.assertLess(len(content.splitlines()), 160, skill_dir.name)
            self.assertTrue(any((skill_dir / "references").iterdir()), skill_dir.name)

    def test_pilot_remediation_contracts_are_explicit(self):
        required = {
            "zju-literature-search": ("Incomplete-Input Fallback", "DOI-first", "identifier_missing", "query ID"),
            "zju-fulltext-access": ("safe interactive route", "CARSI", "cookie or session token"),
            "zju-reference-audit": ("Bibliographic identity: not assessed", "Claim support", "user-provided study design"),
            "zju-paper-reader": ("lawful full text", "$zju-fulltext-access", "coverage table"),
            "zju-experiment-log": ("Correction Fallback", "old_value", "new_value", "recorded_at"),
            "zju-statistics-audit": ("explicit severity", "pseudoreplication", "critical"),
            "zju-scientific-writing": ("Missing-Text Fallback", "not_yet_verifiable", "pending_source_text"),
        }
        for skill, phrases in required.items():
            content = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertIn(phrase, content, f"{skill}: {phrase}")

    def test_scripts_have_no_network_or_shell_execution(self):
        banned = ("import requests", "import urllib", "http.client", "subprocess", "os.system", "shell=True")
        for path in (ROOT / "skills").glob("*/scripts/*.py"):
            content = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, content, f"{path.name}: {token}")

    def test_eval_validator_cli(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "evals/validate_suite.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_portfolio_quality_audit_cli(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "evals/audit_skill_portfolio.py"), "--minimum", "80"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_static_security_scan_cli(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/security_scan.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
