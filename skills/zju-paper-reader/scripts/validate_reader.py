#!/usr/bin/env python3
"""Validate structural coverage of a saved paper-reader Markdown file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


COMMON_PATTERNS = {
    "source_identity": r"(?im)^#+\s*(source identity|文献身份|来源信息)",
    "coverage": r"(?im)^#+\s*(coverage|覆盖情况|阅读覆盖)",
    "source_anchors": r"(?im)^#+\s*(source anchors|来源锚点|原文锚点)",
    "page_anchor": r"(?i)(\[?p(?:age)?\.?\s*\d+|第\s*\d+\s*页)",
}

MODE_PATTERNS = {
    "paper-card": {
        "research_question": r"(?im)^#+\s*(research question|研究问题)",
        "evidence_chain": r"(?im)^#+\s*(evidence chain|证据链)",
        "limitations": r"(?im)^#+\s*(limitations|局限|边界条件)",
    },
    "bilingual-reader": {
        "english_content": r"(?im)^#+\s*(english|英文|original)",
        "chinese_content": r"(?im)^#+\s*(chinese|中文|中文解释)",
        "section_alignment": r"(?im)^#+\s*(section|章节)",
    },
}


def validate(content: str, mode: str) -> dict[str, object]:
    patterns = {**COMMON_PATTERNS, **MODE_PATTERNS[mode]}
    checks = {name: bool(re.search(pattern, content)) for name, pattern in patterns.items()}
    lower = content.casefold()
    checks["figure_inventory"] = "figure" in lower or "图" in content
    checks["table_inventory"] = "table" in lower or "表" in content
    checks["equation_inventory"] = "equation" in lower or "公式" in content or "equation_not_applicable" in lower
    missing = [name for name, passed in checks.items() if not passed]
    return {"mode": mode, "valid": not missing, "checks": checks, "missing": missing}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=sorted(MODE_PATTERNS))
    args = parser.parse_args()
    result = validate(args.input.read_text(encoding="utf-8-sig"), args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

\n