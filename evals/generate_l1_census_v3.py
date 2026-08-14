#!/usr/bin/env python3
"""Generate a reproducible L1 engineering census for all portfolio skills.

L1 checks repository contracts only.  The emitted records are compatible with
the protocol-v3 scorer, but they are deliberately kept in a standalone
evidence envelope: they are not evidence for deterministic scientific
function, controlled task quality, or frozen-holdout generalization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml


ROOT = Path(__file__).resolve().parents[1]
LAYER_ID = "L1_contract_conformance"
CHECK_IDS = (
    "skill_format",
    "trigger_boundary",
    "resource_resolution",
    "output_contract",
    "provenance_license",
    "evaluation_mapping",
)
SKILL_NAME_PATTERN = re.compile(r"^zju-[a-z0-9-]+$")
CAPABILITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]+$")
LOCAL_RESOURCE_PATTERN = re.compile(
    r"(?<![\w$/-])((?:references|scripts)/[A-Za-z0-9_.\-/]+)"
)
CROSS_SKILL_RESOURCE_PATTERN = re.compile(
    r"\$(zju-[a-z0-9-]+)/((?:references|scripts)/[A-Za-z0-9_.\-/]+)"
)
TEXT_INPUT_SUFFIXES = {
    ".bib",
    ".csv",
    ".json",
    ".md",
    ".py",
    ".ris",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_INPUT_NAMES = {"LICENSE", "NOTICE"}


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    criteria: str
    evidence_paths: tuple[str, ...]
    failures: tuple[str, ...] = ()


class EvidenceContext:
    """Read repository inputs while retaining a deterministic hash manifest."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.input_hashes: dict[str, str] = {}

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def track(self, path: Path) -> None:
        if not path.is_file():
            return
        relative = self.relative(path)
        content = path.read_bytes()
        if path.suffix.lower() in TEXT_INPUT_SUFFIXES or path.name in TEXT_INPUT_NAMES:
            content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        self.input_hashes[relative] = hashlib.sha256(content).hexdigest()

    def read_text(self, relative: str) -> str:
        path = self.root / relative
        value = path.read_text(encoding="utf-8")
        self.track(path)
        return value

    def read_json(self, relative: str) -> dict[str, Any]:
        value = json.loads(self.read_text(relative))
        if not isinstance(value, dict):
            raise ValueError(f"{relative} must contain a JSON object")
        return value

    def read_yaml(self, relative: str) -> dict[str, Any]:
        value = yaml.safe_load(self.read_text(relative))
        if not isinstance(value, dict):
            raise ValueError(f"{relative} must contain a YAML mapping")
        return value


def _parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    if not text.startswith("---\n"):
        return None, text, "SKILL.md does not start with YAML frontmatter"
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None, text, "SKILL.md frontmatter is not closed"
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        return None, parts[2], f"invalid YAML frontmatter: {exc}"
    if not isinstance(metadata, dict):
        return None, parts[2], "SKILL.md frontmatter must be a mapping"
    return metadata, parts[2], None


def _skill_format(skill_id: str, text: str) -> CheckResult:
    metadata, body, parse_error = _parse_frontmatter(text)
    failures: list[str] = []
    if parse_error:
        failures.append(parse_error)
    if metadata is not None:
        if set(metadata) != {"name", "description"}:
            failures.append("frontmatter must contain only name and description")
        if metadata.get("name") != skill_id:
            failures.append("frontmatter name does not match the skill directory")
        description = metadata.get("description")
        if not isinstance(description, str) or len(description.strip()) < 40:
            failures.append("description is missing or too short to route reliably")
    if not SKILL_NAME_PATTERN.fullmatch(skill_id):
        failures.append("skill directory name is not canonical lower hyphen-case")
    if not body.strip() or not re.search(r"^#\s+\S", body, flags=re.MULTILINE):
        failures.append("instruction body has no top-level heading")
    if len(text.splitlines()) > 500:
        failures.append("SKILL.md exceeds the 500-line progressive-disclosure limit")
    return CheckResult(
        passed=not failures,
        criteria=(
            "SKILL.md is UTF-8, has only name and description in frontmatter, "
            "matches its canonical directory name, and has a concise instruction body."
        ),
        evidence_paths=(f"skills/{skill_id}/SKILL.md",),
        failures=tuple(failures),
    )


def _trigger_boundary(skill_id: str, text: str) -> CheckResult:
    metadata, _, parse_error = _parse_frontmatter(text)
    failures: list[str] = []
    description = metadata.get("description", "") if metadata else ""
    if parse_error or not isinstance(description, str):
        failures.append("frontmatter description could not be read")
    else:
        if re.search(r"\bUse\b", description, flags=re.IGNORECASE) is None:
            failures.append("description lacks an explicit Use trigger")
        if len(description.split()) < 12:
            failures.append("description lacks enough task context for selective triggering")
    boundary_markers = re.compile(
        r"\b(?:do not|never|instead|does not|without|red lines?|boundary|fallback|route gate|mode gate)\b",
        flags=re.IGNORECASE,
    )
    if boundary_markers.search(text) is None:
        failures.append("no negative, fallback, or routing boundary is declared")
    return CheckResult(
        passed=not failures,
        criteria=(
            "The activation description states when to use the skill, and the skill "
            "declares at least one negative, fallback, or routing boundary."
        ),
        evidence_paths=(f"skills/{skill_id}/SKILL.md",),
        failures=tuple(failures),
    )


def _clean_resource(value: str) -> str:
    return value.rstrip("`'\".,;:)")


def _resource_resolution(context: EvidenceContext, skill_id: str, text: str) -> CheckResult:
    failures: list[str] = []
    evidence = {f"skills/{skill_id}/SKILL.md"}
    local_resources = sorted({_clean_resource(item) for item in LOCAL_RESOURCE_PATTERN.findall(text)})
    cross_resources = sorted(
        {(target, _clean_resource(relative)) for target, relative in CROSS_SKILL_RESOURCE_PATTERN.findall(text)}
    )
    if not local_resources and not cross_resources:
        failures.append("SKILL.md does not disclose any reusable reference or script")
    for relative in local_resources:
        path = context.root / "skills" / skill_id / relative
        evidence.add(f"skills/{skill_id}/{relative}")
        if not path.is_file():
            failures.append(f"missing local resource: {relative}")
        else:
            context.track(path)
    for target, relative in cross_resources:
        path = context.root / "skills" / target / relative
        evidence.add(f"skills/{target}/{relative}")
        if not path.is_file():
            failures.append(f"missing cross-skill resource: ${target}/{relative}")
        else:
            context.track(path)

    agent_relative = f"skills/{skill_id}/agents/openai.yaml"
    agent_path = context.root / agent_relative
    evidence.add(agent_relative)
    if not agent_path.is_file():
        failures.append("agents/openai.yaml is missing")
    else:
        try:
            agent = yaml.safe_load(context.read_text(agent_relative))
            interface = agent.get("interface") if isinstance(agent, dict) else None
            required = {"display_name", "short_description", "default_prompt"}
            if not isinstance(interface, dict) or not required.issubset(interface):
                failures.append("agents/openai.yaml lacks the required interface fields")
            elif f"${skill_id}" not in str(interface.get("default_prompt", "")):
                failures.append("default_prompt does not invoke the skill by name")
        except (UnicodeError, yaml.YAMLError) as exc:
            failures.append(f"agents/openai.yaml is unreadable: {exc}")
    return CheckResult(
        passed=not failures,
        criteria=(
            "Every disclosed local or cross-skill resource resolves inside the repository, "
            "and Codex interface metadata invokes the correct skill."
        ),
        evidence_paths=tuple(sorted(evidence)),
        failures=tuple(failures),
    )


def _output_contract(skill_id: str, text: str) -> CheckResult:
    failures: list[str] = []
    match = re.search(
        r"^## Output Contract\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        failures.append("a level-two Output Contract section is missing")
    else:
        body = match.group("body").strip()
        if len(body) < 40:
            failures.append("Output Contract is too short to define a usable artifact")
        if re.search(r"\b(?:return|output|produce|emit|deliver|write|include)\b", body, re.IGNORECASE) is None:
            failures.append("Output Contract does not state a concrete return or artifact obligation")
    return CheckResult(
        passed=not failures,
        criteria=(
            "A non-empty Output Contract names concrete returned content or artifacts instead "
            "of relying on an implicit prose response."
        ),
        evidence_paths=(f"skills/{skill_id}/SKILL.md",),
        failures=tuple(failures),
    )


def _provenance_license(
    context: EvidenceContext,
    skill_id: str,
    sources: dict[str, Any],
) -> CheckResult:
    failures: list[str] = []
    catalog_rows = sources.get("sources", [])
    catalog = {
        row.get("id"): row for row in catalog_rows if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    targets = sources.get("targets", {})
    source_ids = targets.get(skill_id) if isinstance(targets, dict) else None
    if not isinstance(source_ids, list) or not source_ids:
        failures.append("skill has no source mapping in provenance/sources.yaml")
        source_ids = []
    for source_id in source_ids:
        row = catalog.get(source_id)
        if row is None:
            failures.append(f"unknown provenance source: {source_id}")
            continue
        if not isinstance(row.get("repository"), str) or not row["repository"].startswith("https://"):
            failures.append(f"{source_id}: repository URL is missing")
        commit = row.get("commit")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            failures.append(f"{source_id}: pinned forty-character commit is missing")
        if not isinstance(row.get("license"), str) or not row["license"].strip():
            failures.append(f"{source_id}: license declaration is missing")
    for relative in ("LICENSE", "NOTICE"):
        path = context.root / relative
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(f"root {relative} is missing or empty")
        else:
            context.track(path)
    return CheckResult(
        passed=not failures,
        criteria=(
            "The skill maps to at least one declared upstream source with repository, pinned "
            "commit, and license, while the distribution provides LICENSE and NOTICE."
        ),
        evidence_paths=("LICENSE", "NOTICE", "provenance/sources.yaml"),
        failures=tuple(failures),
    )


def _evaluation_mapping(skill_id: str, matrix: dict[str, Any]) -> CheckResult:
    failures: list[str] = []
    rows = [row for row in matrix.get("skills", []) if isinstance(row, dict) and row.get("skill_id") == skill_id]
    if len(rows) != 1:
        failures.append(f"expected exactly one evaluation row, found {len(rows)}")
    else:
        row = rows[0]
        capabilities = row.get("capabilities")
        if not isinstance(capabilities, list) or len(capabilities) < 5:
            failures.append("evaluation row has fewer than five capabilities")
            capabilities = []
        identifiers = [item.get("id") for item in capabilities if isinstance(item, dict)]
        if len(identifiers) != len(capabilities) or len(set(identifiers)) != len(identifiers):
            failures.append("capability IDs are missing or duplicated")
        for item in capabilities:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            criterion = item.get("criterion")
            if not isinstance(identifier, str) or CAPABILITY_ID_PATTERN.fullmatch(identifier) is None:
                failures.append(f"invalid capability ID: {identifier!r}")
            if not isinstance(criterion, str) or len(criterion.strip()) < 20:
                failures.append(f"{identifier!r}: criterion is not operationally specific")
        baselines = row.get("strongest_open_source_baseline_candidates")
        if not isinstance(baselines, list) or not baselines or not all(isinstance(item, str) and item for item in baselines):
            failures.append("no strongest-open-source baseline candidate is declared")
    return CheckResult(
        passed=not failures,
        criteria=(
            "Exactly one protocol-v3 row declares at least five unique operational capability "
            "criteria and at least one open-source baseline candidate."
        ),
        evidence_paths=("evals/skill-evaluation-matrix-v3.json",),
        failures=tuple(failures),
    )


def _input_tree_digest(input_hashes: dict[str, str]) -> str:
    canonical = "".join(f"{path}\0{digest}\n" for path, digest in sorted(input_hashes.items()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def to_results_bundle(
    census: dict[str, Any],
    protocol: dict[str, Any],
    matrix: dict[str, Any],
    *,
    run_id: str,
    skill_commit: str,
) -> dict[str, Any]:
    """Bind census records to a protocol-v3 result envelope.

    This adapter adds only integrity bindings.  It never synthesizes baseline,
    rater, execution, or holdout evidence.  Consequently an L1-only bundle must
    remain Beta with every official score withheld.
    """

    protocol_hash = hashlib.sha256(
        json.dumps(protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    matrix_hash = hashlib.sha256(
        json.dumps(matrix, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    records: list[dict[str, Any]] = []
    for source in census["records"]:
        fingerprint = hashlib.sha256(
            f"{source['skill_id']}\0{source['case_id']}\0{census['input_tree_sha256']}".encode("utf-8")
        ).hexdigest()
        evidence_hash = hashlib.sha256(
            json.dumps(
                {
                    "skill_id": source["skill_id"],
                    "case_id": source["case_id"],
                    "passed": source["passed"],
                    "input_tree_sha256": census["input_tree_sha256"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        records.append({
            **source,
            "run_id": run_id,
            "skill_commit": skill_commit,
            "protocol_sha256": protocol_hash,
            "capability_matrix_sha256": matrix_hash,
            "case_fingerprint": fingerprint,
            "evidence_sha256": evidence_hash,
        })
    return {
        "$schema": "portfolio-results-v3.schema.json",
        "schema_version": "3.0",
        "protocol_id": protocol["protocol_id"],
        "run_id": run_id,
        "skill_commit": skill_commit,
        "protocol_sha256": protocol_hash,
        "capability_matrix_sha256": matrix_hash,
        # No run-manifest, baseline, or rater packet exists for an engineering
        # census.  Those bindings become mandatory only for L3/L4 task records.
        "run_manifest_sha256": None,
        "baseline_selection": {},
        "rater_precommit": None,
        "holdout_declaration": {
            "frozen": False,
            "unseen": False,
            "first_attempt": False,
            "independent_administration": False,
            "lock_sha256": None,
            "case_document_sha256": None,
            "case_fingerprints_sha256": None,
            "first_attempt_registry_sha256": None,
            "allocation_commitment_sha256": None,
            "allocation_reveal_sha256": None,
            "independent_administration_attestation_sha256": None,
        },
        "protocol_deviations": [],
        "records": records,
    }


def build_census(root: Path = ROOT) -> dict[str, Any]:
    context = EvidenceContext(root)
    matrix = context.read_json("evals/skill-evaluation-matrix-v3.json")
    sources = context.read_yaml("provenance/sources.yaml")
    skill_ids = [
        row["skill_id"]
        for row in matrix.get("skills", [])
        if isinstance(row, dict) and isinstance(row.get("skill_id"), str)
    ]
    records: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    checks: dict[str, Callable[[], CheckResult]]

    for skill_id in skill_ids:
        skill_relative = f"skills/{skill_id}/SKILL.md"
        try:
            skill_text = context.read_text(skill_relative)
        except (OSError, UnicodeError) as exc:
            skill_text = ""
            read_failure = str(exc)
        else:
            read_failure = None
        checks = {
            "skill_format": lambda sid=skill_id, text=skill_text: _skill_format(sid, text),
            "trigger_boundary": lambda sid=skill_id, text=skill_text: _trigger_boundary(sid, text),
            "resource_resolution": lambda sid=skill_id, text=skill_text: _resource_resolution(context, sid, text),
            "output_contract": lambda sid=skill_id, text=skill_text: _output_contract(sid, text),
            "provenance_license": lambda sid=skill_id: _provenance_license(context, sid, sources),
            "evaluation_mapping": lambda sid=skill_id: _evaluation_mapping(sid, matrix),
        }
        for check_id in CHECK_IDS:
            result = checks[check_id]()
            failures = list(result.failures)
            if read_failure and check_id in {"skill_format", "trigger_boundary", "resource_resolution", "output_contract"}:
                failures.insert(0, f"could not read {skill_relative}: {read_failure}")
            passed = result.passed and not failures
            records.append({
                "skill_id": skill_id,
                "layer": LAYER_ID,
                "case_id": check_id,
                "stratum": "release_census",
                "capability_ids": [],
                "passed": passed,
                "critical_failure": False,
            })
            observations.append({
                "skill_id": skill_id,
                "check_id": check_id,
                "passed": passed,
                "criteria": result.criteria,
                "evidence_paths": list(result.evidence_paths),
                "failures": failures,
            })

    context.track(context.root / "evals/generate_l1_census_v3.py")
    input_manifest = [
        {"path": path, "sha256": digest}
        for path, digest in sorted(context.input_hashes.items())
    ]
    passed_records = sum(record["passed"] for record in records)
    complete_skills = sum(
        all(record["passed"] for record in records if record["skill_id"] == skill_id)
        and {record["case_id"] for record in records if record["skill_id"] == skill_id} == set(CHECK_IDS)
        for skill_id in skill_ids
    )
    return {
        "schema_version": "3.0-l1-census",
        "protocol_id": "zju-portfolio-twenty-skill-v3",
        "evidence_id": "zju-l1-contract-census-v3",
        "evidence_class": "release_census",
        "scope": "repository_worktree_inputs",
        "skill_commit": None,
        "input_tree_sha256": _input_tree_digest(context.input_hashes),
        "claim_boundary": (
            "Engineering conformance only. These records cannot supply L2, L3, or L4 evidence, "
            "an official capability score, or Stable promotion."
        ),
        "required_check_ids": list(CHECK_IDS),
        "summary": {
            "expected_skills": 20,
            "observed_skills": len(skill_ids),
            "expected_records": 120,
            "observed_records": len(records),
            "passed_records": passed_records,
            "failed_records": len(records) - passed_records,
            "complete_skills": complete_skills,
        },
        "records": records,
        "observations": observations,
        "input_manifest": input_manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("l1-contract-census-v3.json"),
    )
    parser.add_argument(
        "--results-output",
        type=Path,
        help="Optionally write a scorer-compatible L1-only bundle.",
    )
    parser.add_argument(
        "--run-id",
        help="Required with --results-output; identifies the census run.",
    )
    parser.add_argument(
        "--skill-commit",
        help="Required with --results-output; forty-character evaluated commit.",
    )
    arguments = parser.parse_args()
    census = build_census()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(census, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if arguments.results_output is not None:
        if not arguments.run_id:
            parser.error("--run-id is required with --results-output")
        if not isinstance(arguments.skill_commit, str) or re.fullmatch(r"[0-9a-f]{40}", arguments.skill_commit) is None:
            parser.error("--skill-commit must be forty lowercase hexadecimal characters")
        protocol = json.loads((ROOT / "evals/portfolio-protocol-v3.json").read_text(encoding="utf-8"))
        matrix = json.loads((ROOT / "evals/skill-evaluation-matrix-v3.json").read_text(encoding="utf-8"))
        bundle = to_results_bundle(
            census,
            protocol,
            matrix,
            run_id=arguments.run_id,
            skill_commit=arguments.skill_commit,
        )
        arguments.results_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.results_output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(census["summary"], ensure_ascii=False))
    summary = census["summary"]
    return 0 if summary["complete_skills"] == 20 and summary["failed_records"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
