#!/usr/bin/env python3
"""Install, update, inspect, or remove ZJU Research OS skills.

The manager only owns directories listed in its state file. Existing unrelated
skills are never removed. Replacements are backed up before an atomic swap.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_NAME = "zju-research-skills"
STATE_NAME = ".zju-research-skills-install.json"
BACKUP_NAME = ".zju-research-skills-backups"
SUPPORTED_AGENTS = ("codex", "claude", "opencode")
RUNTIME_IMPORT_GROUPS = (
    ("httpx",),
    ("numpy",),
    ("scipy",),
    ("pandas",),
    ("matplotlib",),
    ("fitz", "pdfplumber"),
    ("openpyxl",),
)


class InstallError(RuntimeError):
    pass


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"expected a JSON object in {path}")
    return value


def package_version(root: Path) -> str:
    manifest = _read_json(root / ".codex-plugin" / "plugin.json")
    if manifest.get("name") != PROJECT_NAME:
        raise InstallError("source is not a ZJU Research OS distribution")
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise InstallError("plugin manifest has no version")
    return version


def _expand_agents(values: Iterable[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        if value == "all":
            expanded.extend(SUPPORTED_AGENTS)
        elif value in SUPPORTED_AGENTS:
            expanded.append(value)
        else:
            raise InstallError(f"unsupported agent: {value}")
    return list(dict.fromkeys(expanded))


def target_root(agent: str, scope: str, project_dir: Path | None, home: Path) -> Path:
    if scope == "user":
        return {
            "codex": home / ".codex" / "skills",
            "claude": home / ".claude" / "skills",
            "opencode": home / ".config" / "opencode" / "skills",
        }[agent]
    if project_dir is None:
        raise InstallError("--project-dir is required for project scope")
    base = project_dir.resolve()
    return {
        "codex": base / ".agents" / "skills",
        "claude": base / ".claude" / "skills",
        "opencode": base / ".opencode" / "skills",
    }[agent]


def _skill_dirs(root: Path, packs: Iterable[str]) -> list[tuple[str, Path, str]]:
    result: list[tuple[str, Path, str]] = []
    core = root / "skills"
    if not core.is_dir():
        raise InstallError(f"missing core skills directory: {core}")
    for path in sorted(core.iterdir()):
        if path.is_dir() and (path / "SKILL.md").is_file():
            result.append((path.name, path, "core"))

    requested = list(dict.fromkeys(packs))
    if requested:
        index = _read_json(root / "packs" / "index.json")
        entries = {item["pack_id"]: item for item in index.get("packs", [])}
        unknown = sorted(set(requested) - set(entries))
        if unknown:
            raise InstallError(f"unknown optional pack(s): {', '.join(unknown)}")
        for pack_id in requested:
            pack_root = root / entries[pack_id]["path"]
            manifest = _read_json(pack_root / "pack.json")
            for relative in manifest.get("skills", []):
                path = pack_root / relative
                if not path.is_dir() or not (path / "SKILL.md").is_file():
                    raise InstallError(f"pack {pack_id} has an invalid skill path: {relative}")
                result.append((path.name, path, pack_id))

    names = [name for name, _, _ in result]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise InstallError(f"duplicate skill IDs: {', '.join(duplicates)}")
    return result


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        if "__pycache__" in file_path.parts or file_path.suffix in {".pyc", ".pyo"}:
            continue
        relative = file_path.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        data = file_path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _copy_filter(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}


def _runtime_report() -> dict:
    groups = []
    for alternatives in RUNTIME_IMPORT_GROUPS:
        available = [name for name in alternatives if importlib.util.find_spec(name) is not None]
        groups.append({
            "alternatives": list(alternatives),
            "available": available,
            "satisfied": bool(available),
        })
    return {
        "valid": all(group["satisfied"] for group in groups),
        "groups": groups,
        "optional_not_managed": ["pymatgen for CIF/POSCAR input"],
    }


def _install_runtime(root: Path, *, dry_run: bool) -> dict:
    requirements = root / "requirements-runtime.txt"
    if not requirements.is_file():
        raise InstallError(f"runtime requirements are missing: {requirements}")
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(requirements),
    ]
    if dry_run:
        return {"status": "planned", "command": command, "requirements": str(requirements)}
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise InstallError(completed.stderr.strip() or completed.stdout.strip() or "runtime dependency installation failed")
    report = _runtime_report()
    if not report["valid"]:
        raise InstallError("pip completed but the bundled executor runtime is still incomplete")
    return {"status": "installed", "requirements": str(requirements), **report}


def _load_state(target: Path) -> dict | None:
    path = target / STATE_NAME
    if not path.is_file():
        return None
    state = _read_json(path)
    if state.get("project") != PROJECT_NAME:
        raise InstallError(f"install state at {path} belongs to another project")
    installed = state.get("installed_skills")
    if not isinstance(installed, dict):
        raise InstallError(f"install state at {path} has no valid installed_skills mapping")
    invalid = [
        name
        for name in installed
        if not isinstance(name, str)
        or not name.startswith("zju-")
        or Path(name).name != name
    ]
    if invalid:
        raise InstallError(f"install state at {path} contains invalid skill IDs")
    invalid_metadata = [
        name
        for name, metadata in installed.items()
        if not isinstance(metadata, dict)
        or not isinstance(metadata.get("sha256"), str)
        or len(metadata["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in metadata["sha256"])
        or not isinstance(metadata.get("pack"), str)
    ]
    if invalid_metadata:
        raise InstallError(f"install state at {path} contains invalid skill metadata")
    packs = state.get("packs", [])
    if not isinstance(packs, list) or any(not isinstance(item, str) for item in packs):
        raise InstallError(f"install state at {path} has an invalid packs list")
    return state


def _write_state(target: Path, state: dict) -> None:
    path = target / STATE_NAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _install_target(
    *,
    root: Path,
    target: Path,
    agent: str,
    packs: list[str],
    dry_run: bool,
    runtime_requested: bool = False,
) -> dict:
    skills = _skill_dirs(root, packs)
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
    previous = _load_state(target) or {}
    previous_agent = previous.get("agent")
    if previous_agent not in (None, agent):
        raise InstallError(
            f"install state at {target / STATE_NAME} belongs to agent {previous_agent}, not {agent}"
        )
    previous_owned = set(previous.get("installed_skills", {}).keys())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = target / BACKUP_NAME / timestamp
    actions: list[dict] = []
    installed: dict[str, dict] = {}

    planned: list[tuple[str, Path, str, Path, str, str]] = []
    for name, source, pack in skills:
        destination = target / name
        source_hash = _tree_hash(source)
        if destination.exists() and name not in previous_owned:
            raise InstallError(
                f"refusing to adopt or replace unmanaged path {destination}; move it aside or install elsewhere"
            )
        if destination.is_dir() and _tree_hash(destination) == source_hash:
            actions.append({"skill": name, "action": "unchanged"})
            installed[name] = {"sha256": source_hash, "pack": pack}
            continue
        action = "replace" if destination.exists() else "install"
        actions.append({"skill": name, "action": action})
        installed[name] = {"sha256": source_hash, "pack": pack}
        planned.append((name, source, pack, destination, source_hash, action))

    removed = sorted(previous_owned - set(installed))
    for name in removed:
        destination = target / name
        actions.append({"skill": name, "action": "remove-managed"})

    state = {
        "schema_version": "1.0",
        "project": PROJECT_NAME,
        "version": package_version(root),
        "agent": agent,
        "source": str(root),
        "packs": packs,
        "runtime_requested": runtime_requested,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "installed_skills": installed,
    }
    if not dry_run:
        with tempfile.TemporaryDirectory(prefix=".zju-install-", dir=target) as temporary_dir:
            staging_root = Path(temporary_dir) / "staged"
            rollback_root = Path(temporary_dir) / "rollback"
            staging_root.mkdir()
            for name, source, _pack, _destination, source_hash, _action in planned:
                staged = staging_root / name
                shutil.copytree(source, staged, ignore=_copy_filter)
                if _tree_hash(staged) != source_hash:
                    raise InstallError(f"staged copy failed integrity check for {name}")

            applied: list[tuple[str, str, Path, Path | None]] = []
            try:
                for name, _source, _pack, destination, _source_hash, action in planned:
                    backup = None
                    if destination.exists():
                        backup_root.mkdir(parents=True, exist_ok=True)
                        backup = backup_root / name
                        destination.replace(backup)
                    applied.append((name, action, destination, backup))
                    (staging_root / name).replace(destination)

                for name in removed:
                    destination = target / name
                    if destination.exists():
                        backup_root.mkdir(parents=True, exist_ok=True)
                        backup = backup_root / name
                        destination.replace(backup)
                        applied.append((name, "remove-managed", destination, backup))

                _write_state(target, state)
            except (OSError, InstallError) as exc:
                rollback_root.mkdir(exist_ok=True)
                for name, action, destination, backup in reversed(applied):
                    if action != "remove-managed" and destination.exists():
                        destination.replace(rollback_root / name)
                    if backup is not None and backup.exists():
                        backup.replace(destination)
                raise InstallError(f"installation transaction was rolled back: {exc}") from exc
    return {"agent": agent, "target": str(target), "actions": actions, "state": state}


def _git_pull(root: Path) -> None:
    if not (root / ".git").exists():
        raise InstallError("--pull requires a Git checkout")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=False, capture_output=True, text=True
    )
    if status.returncode != 0:
        raise InstallError(status.stderr.strip() or "cannot inspect Git worktree")
    if status.stdout.strip():
        raise InstallError("refusing to pull into a dirty worktree")
    pulled = subprocess.run(
        ["git", "pull", "--ff-only"], cwd=root, check=False, capture_output=True, text=True
    )
    if pulled.returncode != 0:
        raise InstallError(pulled.stderr.strip() or pulled.stdout.strip() or "git pull failed")


def _run_install(args: argparse.Namespace, *, update: bool = False) -> dict:
    root = Path(args.source).resolve() if args.source else repository_root()
    if update and args.pull and not args.dry_run:
        _git_pull(root)
    agents = _expand_agents(args.agent)
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    project_dir = Path(args.project_dir) if args.project_dir else None
    targets: list[tuple[str, Path, list[str], bool]] = []
    for agent in agents:
        destination = target_root(agent, args.scope, project_dir, home)
        previous = _load_state(destination) if update else None
        selected_packs = list(dict.fromkeys(args.pack))
        if update and not selected_packs and previous:
            selected_packs = list(dict.fromkeys(previous.get("packs", [])))
        selected_runtime = bool(args.with_runtime) or bool(previous and previous.get("runtime_requested", False))
        targets.append((agent, destination, selected_packs, selected_runtime))

    preflight = [
        _install_target(
            root=root,
            target=destination,
            agent=agent,
            packs=selected_packs,
            dry_run=True,
            runtime_requested=selected_runtime,
        )
        for agent, destination, selected_packs, selected_runtime in targets
    ]
    runtime_requested = any(selected_runtime for _, _, _, selected_runtime in targets)
    if args.dry_run:
        reports = preflight
        runtime = (
            _install_runtime(root, dry_run=True)
            if runtime_requested
            else {"status": "not_requested", **_runtime_report()}
        )
    else:
        runtime = (
            _install_runtime(root, dry_run=False)
            if runtime_requested
            else {"status": "not_requested", **_runtime_report()}
        )
        reports = [
            _install_target(
                root=root,
                target=destination,
                agent=agent,
                packs=selected_packs,
                dry_run=False,
                runtime_requested=selected_runtime,
            )
            for agent, destination, selected_packs, selected_runtime in targets
        ]
    return {
        "status": "dry_run" if args.dry_run else "installed",
        "version": package_version(root),
        "runtime": runtime,
        "reports": reports,
    }


def _run_doctor(args: argparse.Namespace) -> dict:
    agents = _expand_agents(args.agent)
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    project_dir = Path(args.project_dir) if args.project_dir else None
    reports = []
    valid = True
    for agent in agents:
        target = target_root(agent, args.scope, project_dir, home)
        state = _load_state(target)
        problems: list[str] = []
        runtime = _runtime_report()
        if state is None:
            problems.append("install state is missing")
        else:
            for name, metadata in state.get("installed_skills", {}).items():
                path = target / name
                if not path.is_dir():
                    problems.append(f"missing skill: {name}")
                elif _tree_hash(path) != metadata.get("sha256"):
                    problems.append(f"modified skill: {name}")
        if not runtime["valid"]:
            missing = [" or ".join(group["alternatives"]) for group in runtime["groups"] if not group["satisfied"]]
            problems.append("missing executor runtime: " + ", ".join(missing))
        valid = valid and not problems
        reports.append({"agent": agent, "target": str(target), "runtime": runtime, "problems": problems})
    return {"status": "ok" if valid else "problems", "valid": valid, "reports": reports}


def _run_uninstall(args: argparse.Namespace) -> dict:
    agents = _expand_agents(args.agent)
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    project_dir = Path(args.project_dir) if args.project_dir else None
    reports = []
    for agent in agents:
        target = target_root(agent, args.scope, project_dir, home)
        state = _load_state(target)
        removed: list[str] = []
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_root = target / BACKUP_NAME / f"uninstall-{timestamp}"
        if state:
            for name in sorted(state.get("installed_skills", {})):
                path = target / name
                if path.exists():
                    backup_root.mkdir(parents=True, exist_ok=True)
                    path.replace(backup_root / name)
                    removed.append(name)
            (target / STATE_NAME).unlink(missing_ok=True)
        reports.append(
            {
                "agent": agent,
                "target": str(target),
                "removed": removed,
                "recoverable_backup": str(backup_root) if removed else None,
            }
        )
    return {"status": "uninstalled", "reports": reports}


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", action="append", choices=(*SUPPORTED_AGENTS, "all"), default=[])
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--project-dir")
    parser.add_argument("--home", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage ZJU Research OS skills")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("install", "update"):
        sub = subparsers.add_parser(command)
        _common(sub)
        sub.add_argument("--source", help="local repository or extracted release directory")
        sub.add_argument("--pack", action="append", default=[], help="optional pack ID")
        sub.add_argument("--with-runtime", action="store_true", help="install pinned executor dependencies")
        sub.add_argument("--dry-run", action="store_true")
        if command == "update":
            sub.add_argument("--pull", action="store_true", help="git pull --ff-only before reinstalling")

    doctor = subparsers.add_parser("doctor")
    _common(doctor)
    uninstall = subparsers.add_parser("uninstall")
    _common(uninstall)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "agent") and not args.agent:
        args.agent = ["codex"]
    try:
        if args.command == "install":
            report = _run_install(args)
        elif args.command == "update":
            report = _run_install(args, update=True)
        elif args.command == "doctor":
            report = _run_doctor(args)
        else:
            report = _run_uninstall(args)
    except (InstallError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.command == "doctor" and not report["valid"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
