#!/usr/bin/env python3
"""Merge stage artifacts transactionally without erasing mission history."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from director_common import canonical_json, load_document, stable_hash, write_document  # noqa: E402
from artifact_contract import validate_artifact  # noqa: E402


def _artifact_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("artifacts"), list):
        return value["artifacts"]
    raise ValueError("Artifacts input must be a list or an object containing an artifacts list")


def merge(
    mission: dict[str, Any],
    artifacts: Any,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(mission, dict):
        raise ValueError("Mission root must be an object")
    incoming = _artifact_list(artifacts)
    existing = mission.get("artifacts", [])
    if not isinstance(existing, list):
        raise ValueError("mission.artifacts must be a list")

    errors: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    duplicate_ids: list[str] = []
    candidate_ids: set[str] = set()
    existing_by_id = {
        item.get("artifact_id"): item
        for item in existing
        if isinstance(item, dict) and isinstance(item.get("artifact_id"), str)
    }

    accepted: list[dict[str, Any]] = []
    for index, artifact in enumerate(incoming):
        if not isinstance(artifact, dict):
            errors.append({"code": "type", "index": index, "message": "Artifact must be an object"})
            continue
        contract = validate_artifact(
            artifact,
            mission_id=mission.get("mission_id"),
            base_dir=base_dir,
        )
        if not contract["valid"]:
            errors.append(
                {
                    "code": "artifact_contract",
                    "index": index,
                    "artifact_id": artifact.get("artifact_id"),
                    "findings": contract["errors"],
                }
            )
            continue
        artifact_id = artifact.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            errors.append({"code": "missing_id", "index": index, "message": "artifact_id is required"})
            continue
        if artifact_id in candidate_ids:
            errors.append({"code": "duplicate_incoming_id", "index": index, "artifact_id": artifact_id})
            continue
        candidate_ids.add(artifact_id)
        prior = existing_by_id.get(artifact_id)
        if prior is not None:
            if canonical_json(prior) == canonical_json(artifact):
                duplicate_ids.append(artifact_id)
            else:
                conflicts.append(
                    {
                        "artifact_id": artifact_id,
                        "code": "id_collision",
                        "message": "Existing artifact was not overwritten; use a new artifact_id and supersedes",
                        "existing_sha256": stable_hash(prior),
                        "incoming_sha256": stable_hash(artifact),
                    }
                )
            continue
        supersedes = artifact.get("supersedes")
        if supersedes is not None:
            targets = supersedes if isinstance(supersedes, list) else [supersedes]
            if artifact_id in targets:
                errors.append({"code": "self_supersedes", "index": index, "artifact_id": artifact_id})
                continue
            unknown = [target for target in targets if target not in existing_by_id]
            if unknown:
                errors.append(
                    {
                        "code": "unknown_superseded_artifact",
                        "index": index,
                        "artifact_id": artifact_id,
                        "unknown_ids": unknown,
                    }
                )
                continue
        accepted.append(copy.deepcopy(artifact))

    # Transactional behavior: one conflict leaves the original mission untouched.
    if errors or conflicts:
        return {
            "valid": False,
            "mission": copy.deepcopy(mission),
            "merged_ids": [],
            "duplicate_ids": sorted(duplicate_ids),
            "conflicts": conflicts,
            "errors": errors,
            "open_loops_preserved": copy.deepcopy(mission.get("open_loops", [])),
        }

    updated = copy.deepcopy(mission)
    updated.setdefault("artifacts", [])
    updated["artifacts"].extend(accepted)
    return {
        "valid": True,
        "mission": updated,
        "merged_ids": [item["artifact_id"] for item in accepted],
        "duplicate_ids": sorted(duplicate_ids),
        "conflicts": [],
        "errors": [],
        "open_loops_preserved": copy.deepcopy(updated.get("open_loops", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-dir", type=Path)
    args = parser.parse_args()
    mission_path = args.mission.resolve()
    result = merge(
        load_document(mission_path),
        load_document(args.artifacts),
        base_dir=args.base_dir or mission_path.parent,
    )
    write_document(args.output, result["mission"] if result["valid"] else result)
    if result["valid"]:
        print(json.dumps({key: result[key] for key in ("valid", "merged_ids", "duplicate_ids")}, ensure_ascii=False, indent=2))
    if not result["valid"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
