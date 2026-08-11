#!/usr/bin/env python3
"""Heuristically scan statistical reporting text; findings require manual review."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


CHECKS = {
    "sample_size_definition": (r"(?i)(\bn\s*=|sample size|样本量|例数)", "No explicit sample-size statement was detected."),
    "effect_size": (r"(?i)(effect size|odds ratio|hazard ratio|risk ratio|mean difference|cohen.?s d|效应量|优势比|风险比|均值差)", "No effect-size term was detected."),
    "confidence_interval": (r"(?i)(confidence interval|\bCI\b|置信区间)", "No confidence interval was detected."),
    "assumption_check": (r"(?i)(assumption|residual|normality|homoscedastic|sphericity|假设检验|残差|正态性|方差齐性|球形性)", "No model-assumption assessment was detected."),
    "multiplicity": (r"(?i)(multiple comparison|multiplicity|false discovery|FDR|Bonferroni|Holm|多重比较|多重检验|错误发现率)", "No multiplicity statement was detected."),
    "missing_data": (r"(?i)(missing data|imputation|complete.case|缺失数据|插补|失访)", "No missing-data handling statement was detected."),
    "replicate_definition": (r"(?i)(biological replicate|technical replicate|experimental unit|生物学重复|技术重复|实验单位)", "No replicate or experimental-unit definition was detected."),
}


def scan(content: str) -> dict[str, object]:
    checks = {name: bool(re.search(pattern, content)) for name, (pattern, _) in CHECKS.items()}
    warnings = [
        {"check": name, "severity": "triage_only", "message": CHECKS[name][1]}
        for name, passed in checks.items()
        if not passed
    ]
    naked_p = re.findall(r"(?i)\bp\s*[<=>]\s*0?\.\d+", content)
    if naked_p and not checks["effect_size"]:
        warnings.append({"check": "p_without_effect_size", "severity": "triage_only", "message": "P values were detected without an effect-size term."})
    return {"status": "triage_only", "checks": checks, "warnings": warnings, "p_value_mentions": naked_p}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = scan(args.input.read_text(encoding="utf-8-sig"))
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
