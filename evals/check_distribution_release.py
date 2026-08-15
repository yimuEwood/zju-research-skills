#!/usr/bin/env python3
"""Fail-closed qualification gate for the stable distribution line.

This gate intentionally does not convert engineering checks into a scientific
capability score. Protocol-v3 capability evidence remains a separate concern.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

import yaml


EXPECTED_VERSION = "1.0.0"
EXPECTED_SKILLS = 20
TEXT_SUFFIXES = {
    ".cfg",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {".gitattributes", ".gitignore", "LICENSE", "NOTICE"}
SKIP_DIRS = {".git", ".pytest_cache", ".venv", "__pycache__", "cache", "results", "venv"}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a mapping")
    return value


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError(f"{path} has no YAML frontmatter")
    raw = text.split("\n---\n", 1)[0][4:]
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path} frontmatter must be a mapping")
    return value


def _check(items: list[dict[str, Any]], check_id: str, passed: bool, evidence: str) -> None:
    items.append({"check_id": check_id, "passed": bool(passed), "evidence": evidence})


def _text_encoding_failures(root: Path) -> list[str]:
    failures: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            failures.append(f"{relative}: {exc}")
            continue
        if "\ufffd" in text:
            failures.append(f"{relative}: contains U+FFFD replacement character")
    return failures


def _discovered_test_count(root: Path) -> int:
    count = 0
    for path in sorted((root / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        count += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        )
    return count


def evaluate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    checks: list[dict[str, Any]] = []

    release = _json(root / "provenance" / "distribution-release-v1.json")
    codex = _json(root / ".codex-plugin" / "plugin.json")
    claude = _json(root / ".claude-plugin" / "plugin.json")
    opencode = _json(root / "opencode.json")
    release_status = _yaml(root / "provenance" / "release-status.yaml")
    portfolio = _yaml(root / "provenance" / "portfolio-manifest.yaml")
    evaluation = _yaml(root / "provenance" / "evaluation-status-v3.yaml")
    matrix = _json(root / "evals" / "skill-evaluation-matrix-v3.json")
    census = _json(root / "evals" / "l1-contract-census-v3.json")
    current_evidence = _json(root / "evals" / "current-portfolio-evidence-v3.json")

    versions = {
        "release": release.get("release"),
        "codex": codex.get("version"),
        "claude": claude.get("version"),
        "release_status": release_status.get("plugin_version"),
        "portfolio": portfolio.get("plugin_version"),
    }
    _check(
        checks,
        "version_consistency",
        set(versions.values()) == {EXPECTED_VERSION},
        json.dumps(versions, sort_keys=True),
    )

    status_ok = (
        release.get("distribution_status") == "stable"
        and release.get("capability_evaluation_status") == "beta"
        and release.get("official_portfolio_score") is None
        and release_status.get("distribution_status") == "stable"
        and release_status.get("capability_evaluation_status") == "beta"
        and portfolio.get("distribution_status") == "stable"
        and portfolio.get("portfolio_status") == "beta"
        and evaluation.get("current_status") == "beta"
        and evaluation.get("official_portfolio_score") is None
    )
    _check(
        checks,
        "distribution_capability_status_separation",
        status_ok,
        "distribution=stable; protocol-v3 capability=beta; official score=null",
    )

    skill_dirs = sorted(path for path in (root / "skills").iterdir() if path.is_dir())
    matrix_ids = sorted(item["skill_id"] for item in matrix.get("skills", []))
    inventory_ok = len(skill_dirs) == EXPECTED_SKILLS and [p.name for p in skill_dirs] == matrix_ids
    _check(
        checks,
        "twenty_skill_inventory",
        inventory_ok,
        f"directories={len(skill_dirs)} matrix={len(matrix_ids)}",
    )

    metadata_failures: list[str] = []
    for directory in skill_dirs:
        skill_file = directory / "SKILL.md"
        runtime_file = directory / "agents" / "openai.yaml"
        if not skill_file.is_file():
            metadata_failures.append(f"{directory.name}: missing SKILL.md")
            continue
        if not runtime_file.is_file():
            metadata_failures.append(f"{directory.name}: missing agents/openai.yaml")
        try:
            metadata = _frontmatter(skill_file)
        except (ValueError, yaml.YAMLError) as exc:
            metadata_failures.append(str(exc))
            continue
        if set(metadata) != {"name", "description"}:
            metadata_failures.append(f"{directory.name}: unexpected frontmatter keys")
        if metadata.get("name") != directory.name:
            metadata_failures.append(f"{directory.name}: name mismatch")
        description = metadata.get("description")
        if not isinstance(description, str) or not (1 <= len(description) <= 1024):
            metadata_failures.append(f"{directory.name}: invalid description")
    _check(
        checks,
        "skill_runtime_metadata",
        not metadata_failures,
        "20/20 SKILL.md plus agents/openai.yaml" if not metadata_failures else "; ".join(metadata_failures),
    )

    census_summary = census.get("summary", {})
    census_ok = (
        census_summary.get("expected_skills") == 20
        and census_summary.get("complete_skills") == 20
        and census_summary.get("passed_records") == 120
        and census_summary.get("failed_records") == 0
    )
    _check(checks, "l1_contract_census", census_ok, json.dumps(census_summary, sort_keys=True))

    l1_records = [
        record
        for record in current_evidence.get("records", [])
        if record.get("layer") == "L1_contract_conformance"
    ]
    l1_skill_ids = {record.get("skill_id") for record in l1_records}
    current_l1_ok = (
        len(l1_records) == 120
        and len(l1_skill_ids) == 20
        and all(record.get("passed") is True for record in l1_records)
        and all(record.get("layer") == "L1_contract_conformance" for record in current_evidence.get("records", []))
        and isinstance(current_evidence.get("skill_commit"), str)
        and len(current_evidence["skill_commit"]) == 40
    )
    _check(
        checks,
        "current_l1_evidence_bundle",
        current_l1_ok,
        "120 commit-bound engineering records; no L2-L4 records",
    )

    script_count = len(list((root / "skills").glob("*/scripts/*.py")))
    _check(
        checks,
        "script_inventory",
        script_count == release["qualification"]["skill_scripts_scanned"],
        f"scripts={script_count}",
    )

    test_count = _discovered_test_count(root)
    expected_test_count = release["qualification"]["offline_tests_discovered"]
    _check(
        checks,
        "offline_test_inventory",
        test_count == expected_test_count,
        f"discovered={test_count} expected={expected_test_count}",
    )

    package_ok = (
        codex.get("skills") == "./skills/"
        and claude.get("name") == codex.get("name")
        and opencode.get("skills") == {"paths": ["./skills"]}
        and codex.get("license") == claude.get("license") == "Apache-2.0"
    )
    _check(checks, "cross_agent_package_manifests", package_ok, "Codex, Claude Code, and OpenCode")

    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    workflow_tokens = [
        "ubuntu-latest",
        "windows-latest",
        '"3.11"',
        '"3.13"',
        "python -m unittest discover",
        "check_distribution_release.py",
        'test "$status" -eq 2',
    ]
    missing_workflow = [token for token in workflow_tokens if token not in workflow]
    _check(
        checks,
        "ci_release_matrix",
        not missing_workflow,
        "4 OS/Python combinations plus distribution and fail-closed capability gates"
        if not missing_workflow
        else "missing: " + ", ".join(missing_workflow),
    )

    encoding_failures = _text_encoding_failures(root)
    _check(
        checks,
        "utf8_repository_text",
        not encoding_failures,
        "all declared text files decode as UTF-8 without replacement characters"
        if not encoding_failures
        else "; ".join(encoding_failures[:10]),
    )

    failed = [item["check_id"] for item in checks if not item["passed"]]
    return {
        "valid": not failed,
        "release": EXPECTED_VERSION,
        "distribution_status": "stable" if not failed else "blocked",
        "capability_evaluation_status": "beta",
        "official_portfolio_score": None,
        "checks": checks,
        "failed_checks": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.root)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
