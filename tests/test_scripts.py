from __future__ import annotations

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

    def test_fulltext_route_and_sensitive_input(self):
        module = load_module("classify_access", "skills/zju-fulltext-access/scripts/classify_access.py")
        self.assertEqual(module.classify({"oa_url": "https://example.org/a"})["route"], "open_access")
        result = module.classify({"database": "Scopus"})
        self.assertEqual(result["route"], "zju_library_interactive")
        self.assertTrue(result["requires_interactive_authentication"])
        blocked = module.classify({"doi": "10.1/a", "password": "do-not-store"})
        self.assertEqual(blocked["status"], "blocked_sensitive_input")

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
        valid = """# Source identity\n# Coverage\n# Research question\n# Evidence chain\n# Limitations\n# Figure inventory\n# Table inventory\n# Equation inventory\n# Source anchors\n[p. 2, Results]\n"""
        self.assertTrue(module.validate(valid, "paper-card")["valid"])
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

    def test_claim_ledger_rejects_unsupported_claims(self):
        module = load_module("check_claim_ledger", "skills/zju-scientific-writing/scripts/check_claim_ledger.py")
        invalid = module.validate([{"claim_id": "C1", "claim": "A", "claim_type": "literature", "status": "supported"}])
        self.assertFalse(invalid["valid"])
        valid = module.validate([{"claim_id": "C1", "claim": "A", "claim_type": "literature", "status": "supported", "evidence_ids": ["R1"], "source_anchor": "p. 2", "citation_verified": True}])
        self.assertTrue(valid["valid"])

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
        invalid = module.validate({
            "run_id": "RUN-2", "experiment_id": "EXP-1", "started_at": "unknown", "status": "completed",
            "inputs": [], "parameters": {}, "software": [], "outputs": [],
            "decision_gate": {"criterion": "QC", "decision": "continue"},
        })
        self.assertFalse(invalid["valid"])

    def test_expansion_discovery_and_design_validators(self):
        monitor = load_module("update_monitor_state", "skills/zju-literature-monitor/scripts/update_monitor_state.py")
        result = monitor.update(
            {"profile_id": "MON-1", "seen": [{"identity": "doi:10.1/old", "first_seen_at": "2026-01-01"}]},
            [{"doi": "10.1/old", "title": "Old"}, {"doi": "https://doi.org/10.1/NEW", "title": "New"}],
            "2026-08-11T09:00:00+08:00",
        )
        self.assertEqual(result["counts"]["new"], 1)
        self.assertEqual(result["counts"]["duplicate"], 1)
        with self.assertRaises(ValueError):
            monitor.update({"profile_id": "MON-1", "token": "secret"}, [], "2026-08-11")

        evidence = load_module("validate_evidence_table", "skills/zju-evidence-synthesis/scripts/validate_evidence_table.py")
        row = {
            "study_id": "S1", "report_id": "R1", "citation_id": "10.1/x", "design": "RCT",
            "population_or_system": "adults", "sample_and_unit": "40 participants",
            "intervention_or_exposure": "A", "comparator": "B", "outcome": "Y", "time_point": "week 4",
            "effect_estimate": 1.2, "uncertainty": "95% CI 1.0 to 1.4", "risk_of_bias": "low",
            "source_anchor": "Table 2",
        }
        self.assertTrue(evidence.validate([row])["valid"])
        self.assertFalse(evidence.validate([{**row, "uncertainty": None}])["valid"])

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

        figure = load_module("validate_figure_manifest", "skills/zju-scientific-figure/scripts/validate_figure_manifest.py")
        figure_plan = {
            "figure_id": "F1", "bounded_conclusion": "A increases Y in the tested system", "route": "data_figure",
            "backend": "python", "source_files": [{"path": "data.csv", "sha256": "a" * 64}],
            "panels": [{"panel_id": "A", "question": "Does A alter Y?", "source_anchor": "data.csv", "panel_type": "scatter", "generated": False}],
            "exports": ["figure.svg"],
        }
        self.assertTrue(figure.validate(figure_plan)["valid"])
        figure_plan["panels"][0]["generated"] = True
        self.assertFalse(figure.validate(figure_plan)["valid"])

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

        response = load_module("validate_response_tracker", "skills/zju-review-response/scripts/validate_response_tracker.py")
        tracker = {"items": [{"comment_id": "R1.1", "source_role": "reviewer_1", "verbatim_comment": "Add control", "action_type": "manuscript_edit", "requested_action": "Clarify control", "evidence_status": "verified", "status": "verified_complete", "response_text": "We clarified it", "manuscript_change": "Added control definition", "location": "Methods, paragraph 2"}]}
        self.assertTrue(response.validate(tracker)["ready"])
        tracker["items"][0]["location"] = "LOCATION_PENDING"
        self.assertFalse(response.validate(tracker)["valid"])

        availability = load_module("validate_data_inventory", "skills/zju-data-availability/scripts/validate_data_inventory.py")
        inventory = {"artifacts": [{"artifact_id": "D1", "description": "source data", "supports_claims": ["C1"], "controller": "authors", "access_route": "public_repository", "status": "ready", "repository": "Example Repository", "identifier": "doi:10.1/data"}]}
        self.assertTrue(availability.validate(inventory)["valid"])
        del inventory["artifacts"][0]["identifier"]
        self.assertFalse(availability.validate(inventory)["valid"])

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

        patent = load_module("validate_feature_ledger", "skills/zju-paper-to-patent/scripts/validate_feature_ledger.py")
        ledger = {"features": [{"feature_id": "FT-1", "normalized_term": "controller", "description": "controls output", "source_ids": ["P1"], "support_state": "explicit", "claim_role": "independent", "confidentiality": "unpublished"}]}
        self.assertTrue(patent.validate(ledger)["valid"])
        ledger["features"][0]["support_state"] = "unsupported"
        self.assertFalse(patent.validate(ledger)["valid"])

        chemistry = load_module("validate_chemistry_records", "skills/zju-chemistry-databases/scripts/validate_chemistry_records.py")
        chemistry_data = {
            "entities": [{"entity_id": "M1", "inchi_key": "ABCDEFGHIJKLMN-ABCDEFGHIJ-A", "identity_status": "resolved"}],
            "records": [{"record_id": "R1", "entity_id": "M1", "property_or_endpoint": "melting point", "value": 100, "unit": "degC", "evidence_type": "experimental", "source_database": "primary literature", "source_anchor": "Table 1"}],
        }
        self.assertTrue(chemistry.validate(chemistry_data)["valid"])
        chemistry_data["records"][0]["entity_id"] = "missing"
        self.assertFalse(chemistry.validate(chemistry_data)["valid"])

        integrity = load_module("audit_provenance_manifest", "skills/zju-research-integrity/scripts/audit_provenance_manifest.py")
        manifest = {"artifacts": [{"artifact_id": "A1", "path": "raw.csv", "role": "raw", "created_at": "2026-08-11T09:00:00+08:00", "custodian": "researcher", "preservation_status": "immutable_original", "sha256": "b" * 64}], "links": []}
        self.assertTrue(integrity.validate(manifest)["valid"])
        manifest["artifacts"][0]["preservation_status"] = "working_copy"
        self.assertFalse(integrity.validate(manifest)["valid"])

    def test_reference_status_freshness_distinguishes_stale_cache(self):
        module = load_module("validate_status_freshness", "skills/zju-reference-audit/scripts/validate_status_freshness.py")
        records = [{"audit_id": "R1", "status_checked_at": "2026-08-01", "status_sources": ["publisher"], "version_status": "version_of_record"}]
        self.assertTrue(module.validate(records, date(2026, 8, 10), 90)["valid"])
        stale = module.validate(records, date(2027, 1, 1), 90)
        self.assertFalse(stale["valid"])
        self.assertEqual(stale["records"][0]["freshness"], "stale")

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
        amendment = json.loads((ROOT / "evals/results/expansion-full-20260811-a/amendments/HYP-09.json").read_text(encoding="utf-8"))
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
\n