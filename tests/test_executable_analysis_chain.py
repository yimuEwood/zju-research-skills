from __future__ import annotations

import csv
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

import scipy.stats as scipy_stats


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analysis_contract(method: str, execution: dict[str, object]) -> dict[str, object]:
    return {
        "contract_id": "AC-EXEC-1",
        "study_id": "STUDY-EXEC-1",
        "question": "What is the prespecified effect or association?",
        "design_stage": "frozen",
        "design": {
            "experimental_unit": "independent sample",
            "observational_unit": "one aggregated measurement per sample",
            "assignment": "randomized" if "ttest" in method else "observational",
            "randomization_unit": "sample" if "ttest" in method else None,
            "grouping_structure": "one row per experimental unit",
        },
        "outcomes": [{
            "outcome_id": "OUT-1",
            "role": "primary",
            "variable": "response",
            "scale": "continuous",
            "timepoint": "endpoint",
        }],
        "analyses": [{
            "analysis_id": "AN-1",
            "outcome_ids": ["OUT-1"],
            "estimand": "prespecified contrast or slope",
            "analysis_population": "all complete experimental units",
            "model_family": method,
            "effect_measure": "mean difference" if "ttest" in method else "slope",
            "uncertainty": "95% confidence interval",
            "missing_data_strategy": "complete case for the prespecified variables",
            "multiplicity_family": "primary_single_test",
            "diagnostics": ["distribution and residual checks"],
            "sensitivity_analyses": ["rank-based sensitivity where implemented"],
            "execution": {"method": method, "result_id": "RES-1", **execution},
        }],
    }


class ExecutableAnalysisChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor = load_module(
            "execute_analysis", "skills/zju-statistics-audit/scripts/execute_analysis.py"
        )
        cls.registry_validator = load_module(
            "reconcile_registry", "skills/zju-statistics-audit/scripts/reconcile_result_registry.py"
        )
        cls.figure_renderer = load_module(
            "render_figure", "skills/zju-scientific-figure/scripts/render_from_registry.py"
        )
        cls.figure_validator = load_module(
            "validate_figure", "skills/zju-scientific-figure/scripts/validate_figure_manifest.py"
        )

    def test_welch_executor_matches_scipy_and_produces_valid_registry(self):
        rows = [
            {"unit": f"C{index}", "group": "control", "value": value}
            for index, value in enumerate([1.0, 2.0, 2.5, 3.5, 4.0], 1)
        ] + [
            {"unit": f"T{index}", "group": "treatment", "value": value}
            for index, value in enumerate([3.0, 4.5, 5.0, 6.0, 7.5, 8.0], 1)
        ]
        contract = analysis_contract("welch_ttest", {
            "value_column": "value",
            "group_column": "group",
            "experimental_unit_column": "unit",
            "reference_group": "control",
            "comparison_group": "treatment",
            "unit": "mg/L",
            "confidence_level": 0.95,
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "data.csv"
            contract_path = base / "analysis-contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            registry, run = self.executor.execute(data_path, contract_path)
            result = registry["results"][0]

            control = [float(row["value"]) for row in rows if row["group"] == "control"]
            treatment = [float(row["value"]) for row in rows if row["group"] == "treatment"]
            expected = scipy_stats.ttest_ind(treatment, control, equal_var=False)
            self.assertAlmostEqual(result["estimate"], sum(treatment) / len(treatment) - sum(control) / len(control), places=12)
            self.assertAlmostEqual(result["test_statistic"], float(expected.statistic), places=12)
            self.assertAlmostEqual(result["p_value"], float(expected.pvalue), places=12)
            self.assertEqual(result["n"], {"experimental_units": 11, "observations": 11})
            self.assertEqual(run["inputs"]["data"]["sha256"], self.executor.sha256_file(data_path))
            report = self.registry_validator.validate(registry, contract)
            self.assertTrue(report["valid"], report)

            paths = self.executor.write_outputs(registry, run, base / "analysis")
            self.assertTrue(paths["registry"].is_file())
            persisted_run = json.loads(paths["run"].read_text(encoding="utf-8"))
            self.assertEqual(
                persisted_run["result_registry"]["sha256"],
                self.executor.sha256_file(paths["registry"]),
            )

    def test_paired_executor_uses_complete_pairs_and_matches_scipy(self):
        reference = [10.0, 12.0, 8.0, 15.0, 9.0]
        comparison = [13.0, 13.5, 11.0, 14.0, 12.0]
        rows: list[dict[str, object]] = []
        for index, (left, right) in enumerate(zip(reference, comparison), 1):
            rows.extend([
                {"unit": f"U{index}", "condition": "before", "value": left},
                {"unit": f"U{index}", "condition": "after", "value": right},
            ])
        rows.append({"unit": "U-MISSING", "condition": "before", "value": 4.0})
        contract = analysis_contract("paired_ttest", {
            "value_column": "value",
            "condition_column": "condition",
            "experimental_unit_column": "unit",
            "reference_group": "before",
            "comparison_group": "after",
            "unit": "AU",
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "paired.csv"
            contract_path = base / "contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            registry, run = self.executor.execute(data_path, contract_path)
        result = registry["results"][0]
        expected = scipy_stats.ttest_rel(comparison, reference)
        self.assertAlmostEqual(result["test_statistic"], float(expected.statistic), places=12)
        self.assertAlmostEqual(result["p_value"], float(expected.pvalue), places=12)
        self.assertEqual(result["n"], {"experimental_units": 5, "observations": 10})
        self.assertEqual(run["analyses"][0]["incomplete_pair_ids_excluded"], ["U-MISSING"])

    def test_ols_executor_and_real_figure_chain(self):
        rows = [
            {"unit": f"U{index}", "dose": float(index), "response": 1.5 + 2.25 * index + offset}
            for index, offset in enumerate([0.2, -0.4, 0.1, 0.5, -0.2, 0.3, -0.1, 0.0], 1)
        ]
        contract = analysis_contract("simple_ols", {
            "x_column": "dose",
            "y_column": "response",
            "experimental_unit_column": "unit",
            "unit": "response units per dose unit",
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "measurements.csv"
            contract_path = base / "analysis-contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            registry, run = self.executor.execute(data_path, contract_path)
            paths = self.executor.write_outputs(registry, run, base)
            result = registry["results"][0]
            expected = scipy_stats.linregress(
                [float(row["dose"]) for row in rows],
                [float(row["response"]) for row in rows],
            )
            self.assertAlmostEqual(result["estimate"], float(expected.slope), places=12)
            self.assertAlmostEqual(result["intercept"], float(expected.intercept), places=12)

            spec = {
                "figure_id": "FIG-EXEC-1",
                "bounded_conclusion": "Response increases with dose in the measured range.",
                "result_id": "RES-1",
                "analysis_id": "AN-1",
                "plot_type": "scatter_regression",
                "x_column": "dose",
                "y_column": "response",
                "experimental_unit_column": "unit",
                "experimental_unit": "independent sample",
                "x_label": "Dose (AU)",
                "y_label": "Response (AU)",
                "formats": ["png", "svg", "pdf"],
                "width_mm": 89,
                "height_mm": 70,
                "dpi": 300,
            }
            spec_path = base / "figure-spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            manifest, manifest_path = self.figure_renderer.render(
                data_path, paths["registry"], spec_path, base / "figure"
            )
            self.assertTrue(manifest_path.is_file())
            self.assertEqual({item["format"] for item in manifest["exports"]}, {"png", "svg", "pdf"})
            for item in manifest["exports"]:
                export_path = manifest_path.parent / item["path"]
                self.assertTrue(export_path.is_file())
                self.assertGreater(export_path.stat().st_size, 500)
                self.assertEqual(item["sha256"], self.figure_renderer.sha256_file(export_path))
            validation = self.figure_validator.validate(manifest, base_dir=manifest_path.parent)
            self.assertTrue(validation["valid"], validation)
            self.assertEqual(validation["verified_exports"], 3)

    def test_figure_rejects_data_not_bound_to_registry(self):
        rows = [
            {"unit": f"U{index}", "dose": float(index), "response": float(index * 2 + 1)}
            for index in range(1, 6)
        ]
        contract = analysis_contract("simple_ols", {
            "x_column": "dose",
            "y_column": "response",
            "experimental_unit_column": "unit",
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "source.csv"
            contract_path = base / "contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            registry, run = self.executor.execute(data_path, contract_path)
            paths = self.executor.write_outputs(registry, run, base)
            altered_path = base / "altered.csv"
            altered_rows = [dict(row) for row in rows]
            altered_rows[0]["response"] = 999.0
            write_csv(altered_path, altered_rows)
            spec_path = base / "figure-spec.json"
            spec_path.write_text(json.dumps({
                "figure_id": "FIG-WRONG-DATA",
                "bounded_conclusion": "Bound result",
                "result_id": "RES-1",
                "plot_type": "scatter_regression",
                "x_column": "dose",
                "y_column": "response",
                "experimental_unit_column": "unit",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match the canonical result registry"):
                self.figure_renderer.render(altered_path, paths["registry"], spec_path, base / "figure")

    def test_figure_rejects_wrong_columns_even_with_same_data_and_counts(self):
        rows = [
            {
                "unit": f"U{index}",
                "dose": float(index),
                "response": float(index * 2 + 1),
                "alternate_response": float(100 - index * 2),
            }
            for index in range(1, 6)
        ]
        contract = analysis_contract("simple_ols", {
            "x_column": "dose",
            "y_column": "response",
            "experimental_unit_column": "unit",
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "data.csv"
            contract_path = base / "contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            registry, run = self.executor.execute(data_path, contract_path)
            paths = self.executor.write_outputs(registry, run, base)
            spec_path = base / "figure-spec.json"
            spec_path.write_text(json.dumps({
                "figure_id": "FIG-WRONG-COLUMN",
                "bounded_conclusion": "Wrong column must not be drawn",
                "result_id": "RES-1",
                "plot_type": "scatter_regression",
                "x_column": "dose",
                "y_column": "alternate_response",
                "experimental_unit_column": "unit",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match the canonical analysis binding"):
                self.figure_renderer.render(data_path, paths["registry"], spec_path, base / "figure")

    def test_group_and_paired_figures_render_with_exact_analysis_bindings(self):
        cases = [
            (
                "welch_ttest",
                [
                    {"unit": f"C{index}", "group": "control", "value": value}
                    for index, value in enumerate([1.0, 2.0, 2.5, 3.5], 1)
                ] + [
                    {"unit": f"T{index}", "group": "treatment", "value": value}
                    for index, value in enumerate([3.0, 4.0, 5.0, 6.0], 1)
                ],
                {
                    "value_column": "value",
                    "group_column": "group",
                    "experimental_unit_column": "unit",
                    "reference_group": "control",
                    "comparison_group": "treatment",
                },
                "group_comparison",
                "group",
                ["control", "treatment"],
            ),
            (
                "paired_ttest",
                [
                    row
                    for index, (before, after) in enumerate([(1.0, 2.0), (2.0, 4.0), (4.0, 5.0), (3.0, 6.0)], 1)
                    for row in (
                        {"unit": f"U{index}", "condition": "before", "value": before},
                        {"unit": f"U{index}", "condition": "after", "value": after},
                    )
                ],
                {
                    "value_column": "value",
                    "condition_column": "condition",
                    "experimental_unit_column": "unit",
                    "reference_group": "before",
                    "comparison_group": "after",
                },
                "paired_comparison",
                "condition",
                ["before", "after"],
            ),
        ]
        for method, rows, execution, plot_type, group_column, group_order in cases:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                data_path = base / "data.csv"
                contract_path = base / "contract.json"
                write_csv(data_path, rows)
                contract = analysis_contract(method, execution)
                contract_path.write_text(json.dumps(contract), encoding="utf-8")
                registry, run = self.executor.execute(data_path, contract_path)
                paths = self.executor.write_outputs(registry, run, base)
                spec_path = base / "figure-spec.json"
                spec_path.write_text(json.dumps({
                    "figure_id": f"FIG-{method}",
                    "bounded_conclusion": "Prespecified comparison",
                    "result_id": "RES-1",
                    "plot_type": plot_type,
                    "value_column": "value",
                    "group_column": group_column,
                    "experimental_unit_column": "unit",
                    "reference_group": group_order[0],
                    "comparison_group": group_order[1],
                    "group_order": group_order,
                    "formats": ["png"],
                }), encoding="utf-8")
                manifest, manifest_path = self.figure_renderer.render(
                    data_path, paths["registry"], spec_path, base / "figure"
                )
                validation = self.figure_validator.validate(manifest, base_dir=manifest_path.parent)
                self.assertTrue(validation["valid"], validation)
                self.assertEqual(validation["verified_exports"], 1)

    def test_unsupported_model_fails_explicitly(self):
        rows = [{"unit": "U1", "value": 1.0}]
        contract = analysis_contract("binomial_glm", {
            "value_column": "value",
            "experimental_unit_column": "unit",
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "data.csv"
            contract_path = base / "contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported execution method"):
                self.executor.execute(data_path, contract_path)

    def test_duplicate_experimental_units_are_not_silently_treated_as_independent(self):
        rows = [
            {"unit": "U1", "group": "control", "value": 1.0},
            {"unit": "U1", "group": "control", "value": 2.0},
            {"unit": "U2", "group": "treatment", "value": 3.0},
            {"unit": "U3", "group": "treatment", "value": 4.0},
        ]
        contract = analysis_contract("welch_ttest", {
            "value_column": "value",
            "group_column": "group",
            "experimental_unit_column": "unit",
            "reference_group": "control",
            "comparison_group": "treatment",
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "data.csv"
            contract_path = base / "contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "multiple observations"):
                self.executor.execute(data_path, contract_path)

    def test_partial_multiplicity_family_is_rejected(self):
        rows = [
            {"unit": "C1", "group": "control", "value": 1.0},
            {"unit": "C2", "group": "control", "value": 2.0},
            {"unit": "T1", "group": "treatment", "value": 3.0},
            {"unit": "T2", "group": "treatment", "value": 4.0},
        ]
        contract = analysis_contract("welch_ttest", {
            "value_column": "value",
            "group_column": "group",
            "experimental_unit_column": "unit",
            "reference_group": "control",
            "comparison_group": "treatment",
        })
        contract["analyses"].append({
            "analysis_id": "AN-2",
            "outcome_ids": ["OUT-1"],
            "estimand": "second prespecified contrast",
            "analysis_population": "all complete experimental units",
            "model_family": "welch_ttest",
            "effect_measure": "mean difference",
            "uncertainty": "95% confidence interval",
            "missing_data_strategy": "complete case",
            "multiplicity_family": "primary_single_test",
            "diagnostics": ["distribution checks"],
            "sensitivity_analyses": ["rank-based check"],
        })
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data_path = base / "data.csv"
            contract_path = base / "contract.json"
            write_csv(data_path, rows)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "planned analyses without execution blocks"):
                self.executor.execute(data_path, contract_path)


if __name__ == "__main__":
    unittest.main()
