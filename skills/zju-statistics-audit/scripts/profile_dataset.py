#!/usr/bin/env python3
"""Create a deterministic bounded EDA profile for a supported local table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from execute_analysis import load_rows, profile_rows, sha256_file


def profile(path: Path) -> dict[str, object]:
    source = path.resolve()
    if not source.is_file():
        raise ValueError(f"data file does not exist: {source}")
    result = profile_rows(load_rows(source))
    return {
        "schema_version": "1.0",
        "source": {
            "path": source.name,
            "format": source.suffix.casefold().lstrip("."),
            "sha256": sha256_file(source),
        },
        **result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = profile(args.data)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "valid": True,
        "rows": payload["rows"],
        "columns": payload["columns"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
