#!/usr/bin/env python3
"""Validate structural and anchor coverage of a saved paper-reader Markdown file."""

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
        "argument_spine": r"(?im)^#+\s*(argument spine|论证主线|论文脊柱)",
        "claim_evidence_map": r"(?im)^#+\s*(claim[- ]evidence map|主张[-— ]证据(?:矩阵|图谱))",
        "methods": r"(?im)^#+\s*(methods|决定解释的关键方法|关键方法)",
        "quantitative_findings": r"(?im)^#+\s*(primary quantitative findings|主要定量结果)",
        "alternatives": r"(?im)^#+\s*(robustness.*alternative|contradictions.*alternative|稳健性.*替代解释|矛盾.*替代解释)",
        "limitations": r"(?im)^#+\s*(limitations|局限|边界条件)",
        "synthesis_handoff": r"(?im)^#+\s*(evidence[- ]synthesis handoff|证据综合交接)",
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
    checks["equation_inventory"] = (
        "equation" in lower or "公式" in content or "equation_not_applicable" in lower
    )
    anchors = re.findall(COMMON_PATTERNS["page_anchor"], content)
    claim_rows = len(re.findall(r"(?im)^\s*(?:[-*]\s*)?(?:claim[_ -]?id|主张\s*id)\s*[:：]", content))
    missing = [name for name, passed in checks.items() if not passed]
    return {
        "mode": mode,
        "valid": not missing,
        "checks": checks,
        "missing": missing,
        "anchor_mentions": len(anchors),
        "claim_rows_detected": claim_rows,
        "note": "This validator checks structure and anchors; it does not judge scientific correctness.",
    }


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
