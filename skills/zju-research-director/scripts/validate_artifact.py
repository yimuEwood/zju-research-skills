#!/usr/bin/env python3
"""Validate one artifact or an artifact list against the shared envelope."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from artifact_contract import validate_artifact  # noqa: E402
from director_common import load_document, write_document  # noqa: E402


def _artifact_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("artifacts"), list):
        return value["artifacts"]
    return [value]


def validate(
    value: Any,
    mission_id: str | None = None,
    base_dir: str | Path | None = None,
    trusted_validation_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    results = [
        validate_artifact(
            item,
            mission_id=mission_id,
            base_dir=base_dir,
            trusted_validation_receipts=trusted_validation_receipts,
        )
        for item in _artifact_list(value)
    ]
    return {
        "valid": all(item["valid"] for item in results),
        "mission_id": mission_id,
        "artifact_count": len(results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--mission-id")
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument(
        "--trusted-validation-receipts",
        type=Path,
        help="attestations from a caller-authenticated independent validation runner",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    input_path = args.input.resolve()
    trusted_validation_receipts = None
    if args.trusted_validation_receipts:
        receipt_document = load_document(args.trusted_validation_receipts)
        trusted_validation_receipts = (
            receipt_document.get("attestations")
            if isinstance(receipt_document, dict)
            else receipt_document
        )
        if not isinstance(trusted_validation_receipts, list):
            raise ValueError(
                "trusted validation receipt document must be a list or an object with attestations"
            )
    result = validate(
        load_document(input_path),
        args.mission_id,
        base_dir=args.base_dir or input_path.parent,
        trusted_validation_receipts=trusted_validation_receipts,
    )
    if args.output:
        write_document(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
