#!/usr/bin/env python3
"""Render manuscript, legend, or table tokens from a canonical result registry."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TOKEN = re.compile(r"\{\{result:([^:{}|]+):([^{}|]+?)(?:\|([^{}]+))?\}\}")
CONSUMER_TYPES = {"abstract", "results", "figure", "table", "supplement", "review_response", "data_availability"}


def _get_path(value: Any, dotted: str) -> tuple[bool, Any]:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _display(value: Any, format_spec: str | None) -> str:
    if isinstance(value, dict) and set(value) >= {"operator", "value"}:
        rendered = _display(value["value"], format_spec)
        return f"{value['operator']}{rendered}"
    if format_spec:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("A numeric format specifier can only be applied to a number.")
        return format(value, format_spec)
    if value is None:
        return "not_applicable"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(
    template: str,
    registry: dict[str, Any],
    consumer_type: str = "results",
    anchor: str = "template",
    use_id_prefix: str = "USE",
) -> dict[str, Any]:
    results = registry.get("results", [])
    result_by_id = {
        str(item.get("result_id")): item
        for item in results
        if isinstance(item, dict) and item.get("result_id")
    }
    issues: list[dict[str, str]] = []
    uses: list[dict[str, Any]] = []
    if consumer_type not in CONSUMER_TYPES:
        issues.append({"token": "consumer_type", "message": f"Unsupported consumer type: {consumer_type}"})
    if not str(anchor).strip():
        issues.append({"token": "anchor", "message": "A consumer anchor is required."})

    def replace(match: re.Match[str]) -> str:
        result_id, field, format_spec = (part.strip() if part else None for part in match.groups())
        result = result_by_id.get(str(result_id))
        token = match.group(0)
        if result is None:
            issues.append({"token": token, "message": f"Unknown result ID: {result_id}"})
            return token
        if result.get("status") == "superseded":
            issues.append({"token": token, "message": f"Result is superseded: {result_id}"})
            return token
        exists, value = _get_path(result, str(field))
        if not exists:
            issues.append({"token": token, "message": f"Unknown result field: {field}"})
            return token
        try:
            rendered = _display(value, format_spec)
        except (ValueError, TypeError) as exc:
            issues.append({"token": token, "message": str(exc)})
            return token
        reported: Any = value if format_spec is None else {"rendered": rendered, "format": format_spec}
        uses.append({
            "use_id": f"{use_id_prefix}-{len(uses) + 1:03d}",
            "consumer_type": consumer_type,
            "anchor": anchor,
            "result_id": result_id,
            "values": {field: reported},
        })
        return rendered

    text = TOKEN.sub(replace, template)
    unresolved = TOKEN.findall(text)
    return {
        "valid": not issues and not unresolved,
        "text": text,
        "registry_version": registry.get("registry_version"),
        "uses": uses,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--uses-output", type=Path)
    parser.add_argument("--consumer-type", choices=sorted(CONSUMER_TYPES), default="results")
    parser.add_argument("--anchor", help="Section, panel, table, supplement, or response anchor; defaults to the template path.")
    parser.add_argument("--use-id-prefix", default="USE")
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8-sig"))
    result = render(
        args.template.read_text(encoding="utf-8-sig"),
        registry,
        consumer_type=args.consumer_type,
        anchor=args.anchor or str(args.template),
        use_id_prefix=args.use_id_prefix,
    )
    if result["valid"]:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result["text"], encoding="utf-8")
    if args.uses_output:
        args.uses_output.parent.mkdir(parents=True, exist_ok=True)
        args.uses_output.write_text(json.dumps({key: value for key, value in result.items() if key != "text"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result["valid"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
