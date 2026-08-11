#!/usr/bin/env python3
"""Plan, run, and packetize the three-arm blind evaluation."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_env import codex_subprocess_env


ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
CONFIG_PATH = EVALS / "experiment.json"
CASES_PATH = EVALS / "cases.json"
UPSTREAM_LOCK_PATH = EVALS / "upstream-lock.json"
UPSTREAM_CACHE = EVALS / "cache/upstream/nature-skills-pinned"
RESULTS = EVALS / "results"
ARM_NAMES = ("no_skill", "upstream_skill", "distilled_skill")
BLIND_LABELS = ("A", "B", "C")
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,80}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts):
        relative = file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(file_path)))
    return digest.hexdigest()


def safe_run_dir(run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run-id must contain only lowercase letters, digits, dot, underscore, and hyphen")
    path = (RESULTS / run_id).resolve()
    prefix = str(RESULTS.resolve()) + os.sep
    if not str(path).startswith(prefix):
        raise ValueError("run-id escaped results directory")
    return path


def codex_executable() -> str:
    executable = shutil.which("codex")
    if not executable:
        raise FileNotFoundError("codex CLI was not found on PATH")
    return executable


def codex_version() -> str:
    environment, _ = codex_subprocess_env()
    completed = subprocess.run([codex_executable(), "--version"], text=True, capture_output=True, check=False, timeout=15, env=environment)
    return (completed.stdout or completed.stderr).strip()


def provider_preflight() -> dict[str, Any]:
    started = time.monotonic()
    environment, proxy_source = codex_subprocess_env()
    last: dict[str, Any] = {}
    for attempt in range(1, 4):
        try:
            completed = subprocess.run(
                [codex_executable(), "doctor", "--json"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=60,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            last = {"reason": "codex doctor timed out"}
            continue
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError:
            last = {
                "reason": "codex doctor did not return JSON",
                "returncode": completed.returncode,
                "stderr": completed.stderr[-1000:],
            }
            continue
        checks = report.get("checks", {})
        auth = checks.get("auth.credentials", {})
        http = checks.get("network.provider_reachability", {})
        websocket = checks.get("network.websocket_reachability", {})
        transport_ready = http.get("status") == "ok" or websocket.get("status") == "ok"
        ready = auth.get("status") == "ok" and transport_ready
        last = {
            "ready": ready,
            "auth_status": auth.get("status"),
            "provider_status": http.get("status"),
            "provider_summary": http.get("summary"),
            "websocket_status": websocket.get("status"),
            "websocket_summary": websocket.get("summary"),
            "proxy_source": proxy_source,
            "attempts": attempt,
            "remediation": None if ready else (http.get("remediation") or websocket.get("remediation")),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        if ready:
            return last
    last.setdefault("ready", False)
    last["proxy_source"] = proxy_source
    last["attempts"] = 3
    last["duration_seconds"] = round(time.monotonic() - started, 3)
    return last


def selected_cases(scope: str, requested_ids: list[str]) -> list[dict[str, Any]]:
    config = read_json(CONFIG_PATH)
    all_cases = read_json(CASES_PATH)["cases"]
    by_id = {case["id"]: case for case in all_cases}
    if requested_ids:
        missing = sorted(set(requested_ids) - set(by_id))
        if missing:
            raise ValueError("Unknown case IDs: " + ", ".join(missing))
        return [by_id[case_id] for case_id in requested_ids]
    if scope == "pilot":
        return [by_id[case_id] for case_id in config["pilot_case_ids"]]
    return all_cases


def verify_upstream() -> dict[str, Any]:
    manifest_path = UPSTREAM_CACHE / "prepared-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Pinned upstream cache is missing; run evals/prepare_upstream.py prepare")
    manifest = read_json(manifest_path)
    lock = read_json(UPSTREAM_LOCK_PATH)
    comparable = ("commit", "file_count", "content_sha256", "selected_skills")
    mismatches = [key for key in comparable if manifest.get(key) != lock.get(key)]
    if mismatches:
        raise RuntimeError("Pinned upstream cache differs from lock: " + ", ".join(mismatches))
    return lock


def copy_instruction_set(case: dict[str, Any], arm: str, destination: Path, config: dict[str, Any]) -> list[dict[str, str]]:
    mapping = config["arm_sources"][case["skill"]]
    copied = []
    if arm == "no_skill":
        return copied
    sources: Iterable[Path]
    if arm == "upstream_skill":
        sources = [UPSTREAM_CACHE / "skills" / name for name in mapping["upstream"]]
    else:
        sources = [ROOT / "skills" / mapping["distilled"]]
    instruction_root = destination / "instructions"
    for index, source in enumerate(sources, 1):
        if not (source / "SKILL.md").is_file():
            raise FileNotFoundError(f"Instruction source is incomplete: {source}")
        target = instruction_root / f"set-{index:02d}"
        shutil.copytree(source, target)
        copied.append({"set": target.name, "sha256": hash_tree(target)})
    return copied


def task_prompt(case: dict[str, Any], has_instructions: bool) -> str:
    instruction = (
        "Before answering, run `rg --files instructions`, then open and follow every file matching "
        "`instructions/set-*/SKILL.md`. Load only the references those entrypoints require for this task. "
        "Do not answer until every matching SKILL.md has been opened successfully. "
        if has_instructions
        else "Solve the task without reading or using any installed or external skill. "
    )
    return (
        "You are an independent evaluation participant. "
        + instruction
        + "Use fixture.json as task input. Do not inspect parent directories. Do not use network access. "
        + "Do not mention instruction-set names, skill names, file paths, evaluation arms, or this evaluation protocol in the answer. "
        + "Do not create or modify files. Return only the answer that should be shown to the user.\n\n"
        + "USER TASK:\n"
        + case["prompt"]
    )


def create_plan(run_id: str, scope: str, requested_ids: list[str]) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id)
    if run_dir.exists():
        raise FileExistsError(f"Run already exists; use a new run-id or the run command to resume: {run_dir}")
    config = read_json(CONFIG_PATH)
    upstream_lock = verify_upstream()
    cases = selected_cases(scope, requested_ids)
    rng = random.Random(config["random_seed"])
    arm_map: dict[str, dict[str, str]] = {}
    task_records = []
    for case in cases:
        arms = list(ARM_NAMES)
        rng.shuffle(arms)
        label_map = dict(zip(BLIND_LABELS, arms, strict=True))
        arm_map[case["id"]] = label_map
        for label in BLIND_LABELS:
            arm = label_map[label]
            task_dir = run_dir / "tasks" / case["id"] / label
            work_dir = task_dir / "work"
            work_dir.mkdir(parents=True, exist_ok=False)
            fixture = {"case_id": case["id"], "domain": case["domain"], "fixture": case["fixture"]}
            write_json(work_dir / "fixture.json", fixture)
            instruction_hashes = copy_instruction_set(case, arm, work_dir, config)
            prompt = task_prompt(case, bool(instruction_hashes))
            (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            task_records.append({
                "case_id": case["id"],
                "blind_label": label,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "fixture_sha256": sha256_file(work_dir / "fixture.json"),
                "instruction_sets": instruction_hashes,
                "status": "planned",
            })
    private = {
        "schema_version": "1.0",
        "run_id": run_id,
        "random_seed": config["random_seed"],
        "arm_map": arm_map,
        "tasks": task_records,
    }
    write_json(run_dir / "private/arm-map.json", private)
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "experiment_id": config["experiment_id"],
        "created_at": utc_now(),
        "scope": scope,
        "case_ids": [case["id"] for case in cases],
        "case_count": len(cases),
        "task_count": len(task_records),
        "model": config["model"],
        "reasoning_effort": config["reasoning_effort"],
        "sandbox": config["sandbox"],
        "web_search": config["web_search"],
        "codex_version": codex_version(),
        "experiment_config_sha256": sha256_file(CONFIG_PATH),
        "cases_sha256": sha256_file(CASES_PATH),
        "upstream_commit": upstream_lock["commit"],
        "upstream_content_sha256": upstream_lock["content_sha256"],
        "tasks": [
            {"case_id": task["case_id"], "blind_label": task["blind_label"], "status": "planned"}
            for task in task_records
        ],
        "status": "planned",
    }
    write_json(run_dir / "run-manifest.json", manifest)
    return manifest


def recursive_usages(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in ("input_tokens", "output_tokens", "cached_input_tokens")):
            yield value
        for child in value.values():
            yield from recursive_usages(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_usages(child)


def parse_usage(events_text: str) -> dict[str, int]:
    latest: dict[str, Any] = {}
    for line in events_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for usage in recursive_usages(event):
            latest = usage
    return {
        "input_tokens": int(latest.get("input_tokens", 0) or 0),
        "cached_input_tokens": int(latest.get("cached_input_tokens", 0) or 0),
        "output_tokens": int(latest.get("output_tokens", 0) or 0),
    }


def instruction_read_audit(task_dir: Path, events_text: str) -> dict[str, Any]:
    required = sorted((task_dir / "work/instructions").glob("set-*/SKILL.md"))
    if not required:
        return {"required": 0, "verified": True, "missing": []}
    successful_commands: list[str] = []
    direct_read_commands = ("get-content", "type ", "more ", "gc ")
    for line in events_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") != "item.completed" or item.get("type") != "command_execution":
            continue
        if item.get("status") != "completed" or item.get("exit_code") != 0:
            continue
        command = str(item.get("command", "")).casefold().replace("\\", "/")
        command = re.sub(r"/+", "/", command)
        direct_read = any(token in command for token in direct_read_commands)
        rg_content_read = "rg " in command and "--files" not in command
        if direct_read or rg_content_read:
            successful_commands.append(command)
    missing = []
    for path in required:
        relative = path.relative_to(task_dir / "work").as_posix().casefold()
        if not any(relative in command for command in successful_commands):
            missing.append(relative)
    return {"required": len(required), "verified": not missing, "missing": missing}


def refresh_protocol_audits(run_dir: Path) -> dict[str, int]:
    counts = {"completed": 0, "protocol_failed": 0, "unchanged": 0}
    for execution_path in sorted((run_dir / "tasks").glob("*/*/execution.json")):
        task_dir = execution_path.parent
        execution = read_json(execution_path)
        if execution.get("status") not in {"completed", "protocol_failed"}:
            counts["unchanged"] += 1
            continue
        events_path = task_dir / "events.jsonl"
        response_path = task_dir / "response.md"
        if not events_path.is_file() or not response_path.is_file() or execution.get("returncode") != 0:
            counts["unchanged"] += 1
            continue
        audit = instruction_read_audit(task_dir, events_path.read_text(encoding="utf-8-sig"))
        status = "completed" if audit["verified"] else "protocol_failed"
        execution["instruction_read_audit"] = audit
        execution["status"] = status
        write_json(execution_path, execution)
        counts[status] += 1
    return counts


def run_task(run_dir: Path, task: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    task_dir = run_dir / "tasks" / task["case_id"] / task["blind_label"]
    response_path = task_dir / "response.md"
    execution_path = task_dir / "execution.json"
    if execution_path.is_file() and read_json(execution_path).get("status") == "completed" and response_path.is_file():
        return {"case_id": task["case_id"], "blind_label": task["blind_label"], "status": "already_completed"}
    prompt = (task_dir / "prompt.txt").read_text(encoding="utf-8")
    command = [
        codex_executable(),
        "-a", "never",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-s", config["sandbox"],
        "-m", config["model"],
        "-c", f'model_reasoning_effort="{config["reasoning_effort"]}"',
        "--json",
        "-o", str(response_path),
        "-",
    ]
    started_at = utc_now()
    started = time.monotonic()
    status = "failed"
    returncode = None
    stdout = ""
    stderr = ""
    try:
        environment, proxy_source = codex_subprocess_env()
        completed = subprocess.run(
            command,
            cwd=task_dir / "work",
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=config["timeout_seconds"],
            env=environment,
        )
        returncode = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
        status = "completed" if returncode == 0 and response_path.is_file() else "failed"
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        status = "timeout"
    duration = round(time.monotonic() - started, 3)
    (task_dir / "events.jsonl").write_text(stdout, encoding="utf-8")
    (task_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    instruction_audit = instruction_read_audit(task_dir, stdout)
    if status == "completed" and not instruction_audit["verified"]:
        status = "protocol_failed"
    execution = {
        "schema_version": "1.0",
        "case_id": task["case_id"],
        "blind_label": task["blind_label"],
        "status": status,
        "started_at": started_at,
        "ended_at": utc_now(),
        "duration_seconds": duration,
        "returncode": returncode,
        "model": config["model"],
        "reasoning_effort": config["reasoning_effort"],
        "sandbox": config["sandbox"],
        "web_search": config["web_search"],
        "proxy_source": proxy_source if "proxy_source" in locals() else "unknown",
        "usage": parse_usage(stdout),
        "response_sha256": sha256_file(response_path) if response_path.is_file() else None,
        "instruction_read_audit": instruction_audit,
    }
    write_json(execution_path, execution)
    return execution


def run_plan(run_id: str, max_runs: int | None, skip_preflight: bool, jobs: int = 1) -> dict[str, Any]:
    if jobs < 1 or jobs > 8:
        raise ValueError("jobs must be between 1 and 8")
    run_dir = safe_run_dir(run_id)
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Run plan not found: {manifest_path}")
    if not skip_preflight:
        preflight = provider_preflight()
        write_json(run_dir / "preflight.json", preflight)
        if not preflight["ready"]:
            return {"status": "preflight_failed", "preflight": preflight, "executed": 0}
    manifest = read_json(manifest_path)
    protocol_refresh = refresh_protocol_audits(run_dir)
    config = read_json(CONFIG_PATH)
    pending = []
    for task in manifest["tasks"]:
        task_dir = run_dir / "tasks" / task["case_id"] / task["blind_label"]
        execution_path = task_dir / "execution.json"
        response_path = task_dir / "response.md"
        if execution_path.is_file() and response_path.is_file() and read_json(execution_path).get("status") == "completed":
            continue
        if max_runs is not None and len(pending) >= max_runs:
            break
        pending.append(task)
    if jobs == 1:
        executed = [run_task(run_dir, task, config) for task in pending]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            executed = list(executor.map(lambda item: run_task(run_dir, item, config), pending))
    completed = sum(
        1
        for task in manifest["tasks"]
        if (run_dir / "tasks" / task["case_id"] / task["blind_label"] / "execution.json").is_file()
        and read_json(run_dir / "tasks" / task["case_id"] / task["blind_label"] / "execution.json").get("status") == "completed"
    )
    status = "completed" if completed == manifest["task_count"] else "partial"
    manifest["status"] = status
    manifest["completed_tasks"] = completed
    manifest["updated_at"] = utc_now()
    write_json(manifest_path, manifest)
    return {"status": status, "executed": len(executed), "completed": completed, "total": manifest["task_count"], "jobs": jobs, "protocol_refresh": protocol_refresh, "results": executed}


def leakage_markers(config: dict[str, Any]) -> list[str]:
    markers = [name.casefold() for mapping in config["arm_sources"].values() for name in mapping["upstream"]]
    markers.extend(mapping["distilled"].casefold() for mapping in config["arm_sources"].values())
    return sorted(set(markers))


def build_packets(run_id: str) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id)
    manifest = read_json(run_dir / "run-manifest.json")
    config = read_json(CONFIG_PATH)
    case_by_id = {case["id"]: case for case in read_json(CASES_PATH)["cases"]}
    markers = leakage_markers(config)
    issues = []
    packets = []
    for case_id in manifest["case_ids"]:
        case = case_by_id[case_id]
        responses = []
        for label in BLIND_LABELS:
            task_dir = run_dir / "tasks" / case_id / label
            response_path = task_dir / "response.md"
            execution_path = task_dir / "execution.json"
            if not response_path.is_file() or not execution_path.is_file():
                issues.append(f"missing:{case_id}:{label}")
                continue
            execution = read_json(execution_path)
            if execution.get("status") != "completed":
                issues.append(f"incomplete:{case_id}:{label}:{execution.get('status')}")
                continue
            response = response_path.read_text(encoding="utf-8-sig")
            found = [marker for marker in markers if marker in response.casefold()]
            if found:
                issues.append(f"arm-leakage:{case_id}:{label}:{','.join(found)}")
            responses.append({"blind_label": label, "response": response})
        if len(responses) == 3:
            packet = {
                "schema_version": "1.0",
                "run_id": run_id,
                "case_id": case_id,
                "domain": case["domain"],
                "user_task": case["prompt"],
                "fixture": case["fixture"],
                "gold_checks": case["gold_checks"],
                "responses": responses,
            }
            packet_path = run_dir / "blind/packets" / f"{case_id}.json"
            write_json(packet_path, packet)
            packets.append({"case_id": case_id, "path": str(packet_path.relative_to(run_dir)), "sha256": sha256_file(packet_path)})
    index = {"schema_version": "1.0", "run_id": run_id, "valid": not issues, "packets": packets, "issues": issues}
    write_json(run_dir / "blind/packet-index.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("preflight")

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--run-id", required=True)
    plan_parser.add_argument("--scope", choices=("pilot", "full"), default="pilot")
    plan_parser.add_argument("--case", action="append", default=[])

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--max-runs", type=int)
    run_parser.add_argument("--jobs", type=int, default=1)
    run_parser.add_argument("--skip-preflight", action="store_true")

    packet_parser = subparsers.add_parser("packetize")
    packet_parser.add_argument("--run-id", required=True)

    args = parser.parse_args()
    try:
        if args.command == "preflight":
            result = provider_preflight()
            exit_code = 0 if result["ready"] else 2
        elif args.command == "plan":
            result = create_plan(args.run_id, args.scope, args.case)
            exit_code = 0
        elif args.command == "run":
            result = run_plan(args.run_id, args.max_runs, args.skip_preflight, args.jobs)
            exit_code = 0 if result["status"] in {"completed", "partial"} else 2
        else:
            result = build_packets(args.run_id)
            exit_code = 0 if result["valid"] else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result = {"status": "error", "error": str(error)}
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
\n