#!/usr/bin/env python3
"""Static safety scan for shipped skill scripts and plugin structure."""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BANNED_IMPORT_ROOTS = {"requests", "urllib", "http", "socket", "ftplib", "subprocess", "paramiko", "selenium", "playwright"}
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "os.system", "os.popen", "subprocess.run", "subprocess.call", "subprocess.Popen"}


def dotted_name(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def scan_script(path: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    content = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as error:
        return [{"severity": "critical", "path": str(path), "line": error.lineno, "message": "Python syntax error"}]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            if name.split(".", 1)[0] in BANNED_IMPORT_ROOTS:
                findings.append({"severity": "high", "path": str(path), "line": node.lineno, "message": f"Network or process module imported: {name}"})
        if isinstance(node, ast.Call):
            name = dotted_name(node.func)
            if name in BANNED_CALLS:
                findings.append({"severity": "critical", "path": str(path), "line": node.lineno, "message": f"Dynamic or shell execution: {name}"})
    return findings


def main() -> int:
    findings: list[dict[str, object]] = []
    core_scripts = (ROOT / "skills").glob("*/scripts/*.py")
    pack_scripts = ROOT.glob("packs/*/skills/*/scripts/*.py")
    scripts = sorted({*core_scripts, *pack_scripts})
    for path in scripts:
        findings.extend(scan_script(path))
    symlinks = [str(path) for path in ROOT.rglob("*") if path.is_symlink()]
    findings.extend({"severity": "high", "path": path, "line": 0, "message": "Symlink is not allowed in the plugin bundle"} for path in symlinks)
    result = {
        "valid": not findings,
        "scripts_scanned": len(scripts),
        "symlinks_found": len(symlinks),
        "findings": findings,
        "scope": "Static checks only; does not replace blind safety evaluation.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
