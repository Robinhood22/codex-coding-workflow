#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from analyze_change_scope import analyze_change_scope
from workflow_state import find_git_root, is_workflow_state_path
from verification_summary import build_verification_summary


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def parse_status(status_output: str) -> dict[str, Any]:
    changed_files: list[str] = []
    staged = 0
    unstaged = 0
    untracked = 0

    for line in status_output.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if is_workflow_state_path(path):
            continue
        changed_files.append(path)

        if code == "??":
            untracked += 1
            continue
        if code[0] != " ":
            staged += 1
        if code[1] != " ":
            unstaged += 1

    return {
        "changed_files": changed_files,
        "staged_count": staged,
        "unstaged_count": unstaged,
        "untracked_count": untracked,
    }


def get_tracking_counts(repo_root: Path) -> tuple[int | None, int | None, str | None]:
    upstream = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        repo_root,
    )
    if upstream.returncode != 0:
        return None, None, None

    upstream_name = upstream.stdout.strip()
    counts = run_git(["rev-list", "--left-right", "--count", f"{upstream_name}...HEAD"], repo_root)
    if counts.returncode != 0:
        return None, None, upstream_name

    parts = counts.stdout.strip().split()
    if len(parts) != 2:
        return None, None, upstream_name

    behind = int(parts[0])
    ahead = int(parts[1])
    return ahead, behind, upstream_name


def summarize_branch(repo_root: Path) -> dict[str, Any]:
    branch = run_git(["branch", "--show-current"], repo_root).stdout.strip() or "HEAD-detached"
    status_output = run_git(["status", "--short"], repo_root).stdout
    status = parse_status(status_output)
    ahead, behind, upstream = get_tracking_counts(repo_root)
    change_scope = analyze_change_scope(repo_root)
    verification_summary = build_verification_summary(repo_root)

    task_loop_status = change_scope.get("task_loop_status", "missing")
    task_state_mode = change_scope.get("task_state_mode", "legacy")
    if task_loop_status == "healthy":
        workflow_state = "healthy"
    elif task_loop_status == "stale":
        workflow_state = "stale_task_loop"
    elif task_loop_status == "invalid":
        workflow_state = "invalid_task_loop"
    else:
        workflow_state = "missing_task_loop"

    verification_state = verification_summary["status"]
    risk_level = change_scope.get("risk_level", "low")
    memory_status = change_scope.get("memory_status", "missing")
    policy_status = change_scope.get("policy_status", "missing")

    blockers: list[str] = []
    if status["changed_files"]:
        blockers.append("Working tree is not clean.")
    if upstream is None:
        blockers.append("Current branch has no upstream tracking branch.")
    if behind not in (None, 0):
        blockers.append(f"Branch is behind upstream by {behind} commit(s).")
    if ahead == 0 and not status["changed_files"]:
        blockers.append("No local changes or commits are ahead of upstream.")
    if workflow_state == "missing_task_loop" and change_scope.get("changed_file_count", 0) > 0:
        blockers.append("Workflow task loop or task streams are missing for active work.")
    if workflow_state == "invalid_task_loop":
        blockers.append("Workflow task loop or task streams are invalid.")
    if workflow_state == "stale_task_loop":
        blockers.append("Workflow task loop or task streams are stale or invalid.")
    if verification_state == "missing" and risk_level in {"medium", "high"}:
        blockers.append("No verification evidence is logged for a medium/high-risk change.")
    if verification_state == "stale":
        blockers.append("Latest verification evidence is stale relative to the active task loop.")
    if verification_state == "invalid":
        blockers.append("Verification log is invalid and should be repaired before shipping.")
    if policy_status == "invalid":
        blockers.append("Workflow policy is invalid and should be repaired before shipping.")
    if memory_status == "invalid":
        blockers.append("Workflow memory is invalid and should be repaired before shipping.")

    recent_log = run_git(["log", "--oneline", "-n", "5"], repo_root).stdout.strip().splitlines()

    return {
        "status": "ok",
        "repo_root": str(repo_root),
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "changed_files": status["changed_files"],
        "staged_count": status["staged_count"],
        "unstaged_count": status["unstaged_count"],
        "untracked_count": status["untracked_count"],
        "recent_commits": recent_log,
        "risk_level": risk_level,
        "workflow_state": workflow_state,
        "task_state_mode": task_state_mode,
        "task_stream_count": change_scope.get("task_stream_count", 0),
        "open_task_stream_count": change_scope.get("open_task_stream_count", 0),
        "primary_stream_id": change_scope.get("primary_stream_id"),
        "verification_state": verification_state,
        "memory_state": memory_status,
        "policy_state": policy_status,
        "verification_summary": verification_summary,
        "change_scope": change_scope,
        "blockers": blockers,
    }


def render_text(summary: dict[str, Any]) -> str:
    if summary["status"] == "not_git":
        return "No git repository detected, so branch readiness cannot be audited."

    lines = [
        f"Branch: {summary['branch']}",
        f"Upstream: {summary['upstream'] or 'none'}",
        f"Ahead/behind: {summary['ahead'] if summary['ahead'] is not None else '?'} / "
        f"{summary['behind'] if summary['behind'] is not None else '?'}",
        "Working tree: "
        f"{len(summary['changed_files'])} file(s), "
        f"{summary['staged_count']} staged, "
        f"{summary['unstaged_count']} unstaged, "
        f"{summary['untracked_count']} untracked",
        "Workflow state: "
        f"{summary.get('workflow_state', 'unknown')} ({summary.get('task_state_mode', 'legacy')})",
        f"Verification state: {summary.get('verification_state', 'unknown')}",
        f"Memory state: {summary.get('memory_state', 'unknown')}",
        f"Policy state: {summary.get('policy_state', 'unknown')}",
        f"Risk level: {summary.get('risk_level', 'unknown')}",
    ]
    if summary.get("task_state_mode") == "streams":
        lines.append(
            "Streams: "
            f"{summary.get('task_stream_count', 0)} total, "
            f"{summary.get('open_task_stream_count', 0)} open, "
            f"primary={summary.get('primary_stream_id') or 'none'}"
        )

    if summary["blockers"]:
        lines.append("Blockers:")
        lines.extend(f"- {blocker}" for blocker in summary["blockers"])
    else:
        lines.append("Blockers: none detected from git state alone.")

    if summary["changed_files"]:
        lines.append("Changed files:")
        lines.extend(f"- {path}" for path in summary["changed_files"])

    if summary["recent_commits"]:
        lines.append("Recent commits:")
        lines.extend(f"- {commit}" for commit in summary["recent_commits"])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize branch ship readiness.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--repo", type=str, default=".", help="Repository path to inspect.")
    args = parser.parse_args()

    base_dir = Path(args.repo).expanduser()
    repo_root = find_git_root(base_dir)
    if repo_root is None:
        summary: dict[str, Any] = {
            "status": "not_git",
            "repo_root": None,
            "branch": None,
            "upstream": None,
            "ahead": None,
            "behind": None,
            "changed_files": [],
            "staged_count": 0,
            "unstaged_count": 0,
            "untracked_count": 0,
            "recent_commits": [],
            "risk_level": "low",
            "workflow_state": "unknown",
            "verification_state": "unknown",
            "memory_state": "unknown",
            "policy_state": "unknown",
            "verification_summary": {},
            "change_scope": {},
            "blockers": ["No git repository detected."],
        }
    else:
        summary = summarize_branch(repo_root)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
