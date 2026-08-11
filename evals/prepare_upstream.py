#!/usr/bin/env python3
"""Fetch the pinned Nature skill directories used by the upstream eval arm."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "evals/experiment.json"
DEFAULT_CACHE = ROOT / "evals/cache/upstream/nature-skills-pinned"


def request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "zju-research-skills-eval/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "zju-research-skills-eval/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def selected_skill_names(config: dict[str, Any]) -> set[str]:
    return {
        name
        for mapping in config["arm_sources"].values()
        for name in mapping["upstream"]
    }


def safe_destination(cache: Path, repo_path: str) -> Path:
    pure = PurePosixPath(repo_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"Unsafe repository path: {repo_path}")
    destination = (cache / Path(*pure.parts)).resolve()
    prefix = str(cache.resolve()) + os.sep
    if not str(destination).startswith(prefix):
        raise ValueError(f"Path escaped cache: {repo_path}")
    return destination


def aggregate_digest(files: list[dict[str, Any]]) -> str:
    payload = "\n".join(f"{item['path']}\0{item['sha256']}" for item in sorted(files, key=lambda item: item["path"]))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_manifest(cache: Path, config: dict[str, Any], paths: list[str], transport: str) -> dict[str, Any]:
    files = []
    for repo_path in sorted(paths):
        destination = safe_destination(cache, repo_path)
        content = destination.read_bytes()
        files.append({
            "path": repo_path,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return {
        "schema_version": "1.0",
        "repository": config["upstream"]["repository"],
        "commit": config["upstream"]["commit"],
        "license": config["upstream"]["license"],
        "selected_skills": sorted(selected_skill_names(config)),
        "transport": transport,
        "file_count": len(files),
        "content_sha256": aggregate_digest(files),
        "files": files,
    }


def prepare_http(cache: Path, config: dict[str, Any]) -> dict[str, Any]:
    commit = config["upstream"]["commit"]
    repo = "Yuan1z0825/nature-skills"
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1"
    tree = request_json(tree_url)
    if tree.get("sha") != commit:
        raise RuntimeError(f"GitHub returned unexpected tree {tree.get('sha')} for {commit}")
    wanted = selected_skill_names(config)
    prefix_set = {f"skills/{name}/" for name in wanted}
    paths = [
        item["path"]
        for item in tree.get("tree", [])
        if item.get("type") == "blob" and (item["path"] == "LICENSE" or any(item["path"].startswith(prefix) for prefix in prefix_set))
    ]
    missing = sorted(name for name in wanted if not any(path.startswith(f"skills/{name}/") for path in paths))
    if missing:
        raise RuntimeError("Pinned tree is missing selected skills: " + ", ".join(missing))
    for repo_path in sorted(paths):
        raw_url = f"https://raw.githubusercontent.com/{repo}/{commit}/{repo_path}"
        content = request_bytes(raw_url)
        destination = safe_destination(cache, repo_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    manifest = build_manifest(cache, config, paths, "github-api+raw")
    manifest_path = cache / "prepared-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def run_git(arguments: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode:
        raise RuntimeError((completed.stderr or completed.stdout).strip() or f"git exited {completed.returncode}")
    return completed.stdout.strip()


def prepare_git(cache: Path, config: dict[str, Any]) -> dict[str, Any]:
    commit = config["upstream"]["commit"]
    wanted = selected_skill_names(config)
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / ".git").exists():
        run_git(["init", "--quiet"], cache)
        run_git(["remote", "add", "origin", config["upstream"]["repository"] + ".git"], cache)
    run_git(["config", "http.version", "HTTP/1.1"], cache)
    run_git(["config", "remote.origin.promisor", "true"], cache)
    run_git(["config", "remote.origin.partialclonefilter", "blob:none"], cache)
    run_git(["sparse-checkout", "init", "--cone"], cache)
    run_git(["sparse-checkout", "set", "LICENSE", *[f"skills/{name}" for name in sorted(wanted)]], cache)
    run_git(["fetch", "--filter=blob:none", "--depth=1", "origin", commit], cache)
    run_git(["checkout", "--detach", "FETCH_HEAD"], cache)
    actual_commit = run_git(["rev-parse", "HEAD"], cache)
    if actual_commit != commit:
        raise RuntimeError(f"Git checkout mismatch: expected {commit}, got {actual_commit}")
    paths = [
        line
        for line in run_git(["ls-files"], cache).splitlines()
        if line == "LICENSE" or any(line.startswith(f"skills/{name}/") for name in wanted)
    ]
    missing = sorted(name for name in wanted if not any(path.startswith(f"skills/{name}/") for path in paths))
    if missing:
        raise RuntimeError("Pinned checkout is missing selected skills: " + ", ".join(missing))
    manifest = build_manifest(cache, config, paths, "git-sparse-checkout")
    (cache / "prepared-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def prepare(cache: Path, transport: str) -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if transport == "http":
        return prepare_http(cache, config)
    if transport == "git":
        return prepare_git(cache, config)
    try:
        return prepare_http(cache, config)
    except (OSError, RuntimeError, urllib.error.URLError):
        return prepare_git(cache, config)


def verify(cache: Path, lock_path: Path | None) -> dict[str, Any]:
    manifest_path = cache / "prepared-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Run prepare first: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    issues = []
    for item in manifest["files"]:
        path = safe_destination(cache, item["path"])
        if not path.is_file():
            issues.append(f"missing:{item['path']}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            issues.append(f"hash:{item['path']}")
    if lock_path and lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if (
            manifest["commit"] != lock["commit"]
            or manifest["file_count"] != lock["file_count"]
            or manifest["content_sha256"] != lock["content_sha256"]
            or manifest["selected_skills"] != lock["selected_skills"]
        ):
            issues.append("tracked-lock-mismatch")
    return {"valid": not issues, "files": len(manifest["files"]), "issues": issues, "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--lock", type=Path, default=ROOT / "evals/upstream-lock.json")
    parser.add_argument("--transport", choices=("auto", "git", "http"), default="git")
    args = parser.parse_args()
    try:
        result = prepare(args.cache, args.transport) if args.command == "prepare" else verify(args.cache, args.lock)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    if args.command == "prepare":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({key: value for key, value in result.items() if key != "manifest"}, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
\n