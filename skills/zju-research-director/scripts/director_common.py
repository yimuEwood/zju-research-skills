#!/usr/bin/env python3
"""Shared, dependency-free helpers for ZJU Research Director scripts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_SCHEMA_PATH = SKILL_DIR / "references" / "mission-schema.yaml"
DEFAULT_REGISTRY_PATH = SKILL_DIR / "references" / "capability-registry.yaml"


def load_json_yaml(path: str | Path) -> dict[str, Any]:
    """Load a JSON-syntax YAML file without a third-party YAML dependency."""

    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise ValueError(f"Required configuration is missing: {target}") from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{target} must use JSON-compatible YAML syntax: line {error.lineno}, column {error.colno}"
        ) from error
    if not isinstance(value, dict):
        raise ValueError(f"Configuration root must be an object: {target}")
    return value


def load_registry(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    registry_path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    raw = load_json_yaml(registry_path)
    skills = raw.get("skills", {})
    if isinstance(skills, list):
        normalized: dict[str, dict[str, Any]] = {}
        for entry in skills:
            if isinstance(entry, dict) and isinstance(entry.get("skill"), str):
                normalized[entry["skill"]] = entry
    elif isinstance(skills, dict):
        normalized = {str(name): entry for name, entry in skills.items() if isinstance(entry, dict)}
    else:
        raise ValueError("capability-registry.yaml field 'skills' must be an object or list")

    # Optional packs remain absent from the core registry unless their skill is
    # actually installed beside the Director. This keeps the 20-Skill core
    # deterministic while allowing the same planner to discover selected packs.
    optional = raw.get("optional_skills", {})
    if isinstance(optional, dict) and registry_path.name == "capability-registry.yaml":
        skill_root = registry_path.resolve().parents[2]
        for name, entry in optional.items():
            if (
                isinstance(name, str)
                and isinstance(entry, dict)
                and (skill_root / name / "SKILL.md").is_file()
            ):
                normalized[name] = entry
    return normalized


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def autonomy_rank(level: str) -> int:
    ranks = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
    if level not in ranks:
        raise ValueError(f"Unknown autonomy level: {level}")
    return ranks[level]


def load_document(path: str | Path) -> Any:
    target = Path(path)
    try:
        return json.loads(target.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Mission and artifact files must use JSON or JSON-compatible YAML: {target}, "
            f"line {error.lineno}, column {error.colno}"
        ) from error


def write_document(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
