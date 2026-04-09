#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from workflow_state import (
    classify_risk,
    find_git_root,
    get_memory_candidate_state,
    find_workspace_root,
    get_memory_status,
    get_memory_sync_state,
    get_shared_memory_status,
    get_task_loop_status,
    get_verification_state,
    is_workflow_state_path,
    load_policy,
    should_refresh_memory,
    verification_required_for,
)


CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}
CONFIG_EXTENSIONS = {".env", ".ini", ".json", ".toml", ".yaml", ".yml"}
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt"}


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def find_first_value(payload: Any, keys: set[str]) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value:
                return value
            nested = find_first_value(value, keys)
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = find_first_value(item, keys)
            if nested:
                return nested
    return None


def gather_paths(payload: Any, keys: set[str]) -> list[Path]:
    paths: list[Path] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str):
                candidate = Path(value).expanduser()
                if candidate.is_absolute():
                    paths.append(candidate)
            paths.extend(gather_paths(value, keys))
    elif isinstance(payload, list):
        for item in payload:
            paths.extend(gather_paths(item, keys))
    return paths


def resolve_base_dir(payload: Any) -> Path:
    cwd_value = find_first_value(payload, {"cwd", "working_directory"})
    if isinstance(cwd_value, str):
        cwd_path = Path(cwd_value).expanduser()
        if cwd_path.is_absolute():
            return cwd_path

    for candidate in gather_paths(
        payload,
        {"file_path", "path", "target_file", "relative_workspace_path"},
    ):
        if candidate.exists():
            return candidate.parent if candidate.is_file() else candidate
        if candidate.suffix:
            return candidate.parent
        return candidate

    return Path.cwd()


def parse_status_paths(status_output: str) -> set[str]:
    paths: set[str] = set()
    for line in status_output.splitlines():
        if not line.strip():
            continue
        entry = line[3:] if len(line) > 3 else line
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        cleaned = entry.strip()
        if is_workflow_state_path(cleaned):
            continue
        paths.add(cleaned)
    return paths


def parse_numstat(numstat_output: str) -> tuple[int, int]:
    inserted = 0
    deleted = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_raw, del_raw = parts[0], parts[1]
        if add_raw.isdigit():
            inserted += int(add_raw)
        if del_raw.isdigit():
            deleted += int(del_raw)
    return inserted, deleted


def classify_paths(paths: set[str]) -> dict[str, int]:
    categories = {"code": 0, "config": 0, "docs": 0, "tests": 0, "other": 0}
    for raw_path in paths:
        name = raw_path.lower()
        suffix = Path(raw_path).suffix.lower()
        if (
            "/test" in name
            or "\\test" in name
            or "/tests" in name
            or "\\tests" in name
            or name.endswith("_test.py")
            or name.endswith(".test.ts")
            or name.endswith(".test.tsx")
            or name.endswith(".spec.ts")
            or name.endswith(".spec.tsx")
            or name.endswith(".spec.js")
            or name.endswith(".test.js")
        ):
            categories["tests"] += 1
        elif suffix in CODE_EXTENSIONS:
            categories["code"] += 1
        elif suffix in CONFIG_EXTENSIONS:
            categories["config"] += 1
        elif suffix in DOC_EXTENSIONS:
            categories["docs"] += 1
        else:
            categories["other"] += 1
    return categories


def determine_recommended_skills(
    needs_state_repair: bool,
    task_loop_needed: bool,
    task_loop_stale: bool,
    memory_refresh_needed: bool,
    verification_recommended: bool,
    risk_level: str,
) -> list[str]:
    skills: list[str] = []
    if needs_state_repair:
        skills.append("workflow-state-repair")
    if task_loop_needed or task_loop_stale:
        skills.append("execution-task-loop")
    if memory_refresh_needed:
        skills.append("project-memory-sync")
    if risk_level in {"medium", "high"}:
        skills.append("policy-risk-check")
    if verification_recommended:
        skills.append("verify-change")
    return skills


def analyze_change_scope(base_dir: Path) -> dict[str, Any]:
    workspace_root = find_workspace_root(base_dir)
    policy_info = load_policy(base_dir)
    policy = policy_info["data"]
    memory = get_memory_status(base_dir)
    shared_memory = get_shared_memory_status(base_dir)
    memory_candidates = get_memory_candidate_state(base_dir)
    memory_sync = get_memory_sync_state(base_dir)
    task_loop = get_task_loop_status(base_dir, policy)
    verification = get_verification_state(base_dir, task_loop)
    needs_state_repair = (
        memory["status"] == "invalid"
        or shared_memory["status"] == "invalid"
        or memory_candidates["status"] == "invalid"
        or memory_sync["status"] == "invalid"
        or policy_info["status"] == "invalid"
        or task_loop["status"] == "invalid"
        or verification["status"] == "invalid"
    )
    repo_root = find_git_root(base_dir)
    if repo_root is None:
        recommended_skills = determine_recommended_skills(
            needs_state_repair=needs_state_repair,
            task_loop_needed=False,
            task_loop_stale=task_loop["stale"],
            memory_refresh_needed=False,
            verification_recommended=False,
            risk_level="low",
        )
        return {
            "status": "not_git",
            "base_dir": str(base_dir),
            "workspace_root": str(workspace_root),
            "changed_files": [],
            "changed_file_count": 0,
            "total_changed_lines": 0,
            "task_loop_needed": False,
            "verification_recommended": False,
            "risk_level": "low",
            "risk_reasons": ["No git repository detected."],
            "memory_status": memory["status"],
            "shared_memory_status": shared_memory["status"],
            "memory_candidates_status": memory_candidates["status"],
            "memory_candidates_pending": memory_candidates["pending_count"],
            "memory_sync_status": memory_sync["status"],
            "memory_refresh_needed": False,
            "policy_status": policy_info["status"],
            "task_loop_status": task_loop["status"],
            "task_loop_stale": task_loop["stale"],
            "verification_state": verification["status"],
            "verification_log_present": verification["entry_count"] > 0,
            "state_repair_needed": needs_state_repair,
            "recommended_skills": recommended_skills,
            "policy": {
                "path": policy_info["path"],
                "used_default": policy_info["used_default"],
                "status": policy_info["status"],
                "errors": policy_info["errors"],
            },
            "categories": {"code": 0, "config": 0, "docs": 0, "tests": 0, "other": 0},
        }

    status = run_git(["status", "--short"], repo_root)
    paths = parse_status_paths(status.stdout)

    diff_head = run_git(["diff", "--numstat", "HEAD"], repo_root)
    if diff_head.returncode == 0:
        inserted, deleted = parse_numstat(diff_head.stdout)
    else:
        inserted = 0
        deleted = 0
        for diff_args in (["diff", "--numstat"], ["diff", "--cached", "--numstat"]):
            diff_result = run_git(diff_args, repo_root)
            if diff_result.returncode == 0:
                add, remove = parse_numstat(diff_result.stdout)
                inserted += add
                deleted += remove

    categories = classify_paths(paths)
    changed_file_count = len(paths)
    total_changed_lines = inserted + deleted
    task_loop_needed = changed_file_count >= int(policy["task_loop"]["multi_file_threshold"])
    risk = classify_risk(changed_file_count, total_changed_lines, categories, policy)
    risk_level = risk["risk_level"]
    memory_refresh_needed = should_refresh_memory(
        changed_file_count,
        categories,
        risk_level,
        policy,
    ) or memory_candidates["pending_count"] > 0
    verification_recommended = verification_required_for(risk_level, policy) or (
        total_changed_lines >= int(policy["verification"]["changed_lines_threshold"])
    )
    task_loop_stale = task_loop["status"] in {"stale", "invalid"}
    recommended_skills = determine_recommended_skills(
        needs_state_repair=needs_state_repair,
        task_loop_needed=task_loop_needed,
        task_loop_stale=task_loop_stale,
        memory_refresh_needed=memory_refresh_needed,
        verification_recommended=verification_recommended,
        risk_level=risk_level,
    )

    return {
        "status": "ok",
        "repo_root": str(repo_root),
        "workspace_root": str(workspace_root),
        "changed_files": sorted(paths),
        "changed_file_count": changed_file_count,
        "inserted_lines": inserted,
        "deleted_lines": deleted,
        "total_changed_lines": total_changed_lines,
        "task_loop_needed": task_loop_needed,
        "verification_recommended": verification_recommended,
        "risk_level": risk_level,
        "risk_reasons": risk["reasons"],
        "memory_status": memory["status"],
        "shared_memory_status": shared_memory["status"],
        "memory_candidates_status": memory_candidates["status"],
        "memory_candidates_pending": memory_candidates["pending_count"],
        "memory_sync_status": memory_sync["status"],
        "memory_refresh_needed": memory_refresh_needed,
        "policy_status": policy_info["status"],
        "task_loop_status": task_loop["status"],
        "task_loop_stale": task_loop_stale,
        "verification_state": verification["status"],
        "verification_log_present": verification["entry_count"] > 0,
        "state_repair_needed": needs_state_repair,
        "recommended_skills": recommended_skills,
        "policy": {
            "path": policy_info["path"],
            "used_default": policy_info["used_default"],
            "status": policy_info["status"],
            "errors": policy_info["errors"],
        },
        "categories": categories,
    }


def render_summary(result: dict[str, Any]) -> str:
    if result["status"] == "not_git":
        return "No git repository detected, so change-scope analysis is unavailable."

    categories = result["categories"]
    category_summary = ", ".join(
        f"{name}={count}" for name, count in categories.items() if count
    ) or "no categorized files"
    return (
        "Changed files: {count}\n"
        "Changed lines: {lines} (+{added}/-{removed})\n"
        "Categories: {categories}\n"
        "Risk: {risk}\n"
        "Task loop: {task_loop}\n"
        "Verification log: {verification}"
    ).format(
        count=result["changed_file_count"],
        lines=result["total_changed_lines"],
        added=result["inserted_lines"],
        removed=result["deleted_lines"],
        categories=category_summary,
        risk=result["risk_level"],
        task_loop=result.get("task_loop_status", "unknown"),
        verification=result.get("verification_state", "unknown"),
    )


def render_hook_message(result: dict[str, Any]) -> str:
    if result["status"] != "ok":
        return ""

    reminders: list[str] = []
    count = result["changed_file_count"]
    lines = result["total_changed_lines"]

    if result["task_loop_needed"]:
        reminders.append(
            "[codex-coding-workflows] Multi-file change detected "
            f"({count} files, {lines} changed lines). Refresh the task loop so it still "
            "matches the real implementation steps and current scope."
        )
    elif result["task_loop_stale"]:
        reminders.append(
            "[codex-coding-workflows] The repo-local task loop looks stale or invalid. "
            "Refresh it so the active step matches the current implementation."
        )

    if result["state_repair_needed"]:
        reminders.append(
            "[codex-coding-workflows] Repo-local workflow state looks invalid. "
            "Run workflow-state-repair before relying on `.codex-workflows/` for planning, "
            "verification, or ship-readiness decisions."
        )

    if result["memory_refresh_needed"]:
        reminders.append(
            "[codex-coding-workflows] Durable project context likely changed or queued "
            "memory candidates are pending. Run project-memory-sync auto-refresh so "
            "local and shared workflow memory stay current."
        )

    if result["verification_recommended"]:
        reminders.append(
            "[codex-coding-workflows] "
            f"{result['risk_level'].capitalize()}-risk change set detected. Before finalizing, "
            "run verify-change so the closeout includes commands, observed output, and a "
            "PASS, FAIL, or PARTIAL verdict."
        )

    return "\n".join(reminders)


def load_payload_from_stdin() -> Any:
    if sys.stdin.isatty():
        return {}

    raw = sys.stdin.read().strip()
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_stdin": raw}


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize repo change scope.")
    parser.add_argument("--hook", action="store_true", help="Emit hook reminder text only.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--repo", type=str, default=None, help="Base directory to inspect.")
    args = parser.parse_args()

    payload = load_payload_from_stdin() if args.hook else {}
    base_dir = Path(args.repo).expanduser() if args.repo else resolve_base_dir(payload)
    result = analyze_change_scope(base_dir)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if args.hook:
        message = render_hook_message(result)
        if message:
            print(message)
        return 0

    print(render_summary(result))
    if result["status"] == "ok" and result["changed_files"]:
        print("\nFiles:")
        for path in result["changed_files"]:
            print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
