#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from workflow_state import (
    append_reasoning_hotspot_entry,
    build_recurring_reasoning_hotspot_summary,
    classify_risk,
    dedupe_non_empty_strings,
    ensure_state_files,
    find_git_root,
    get_memory_candidate_state,
    find_workspace_root,
    get_buglog_state,
    get_memory_status,
    get_memory_sync_state,
    get_reasoning_hotspot_state,
    get_state_paths,
    get_shared_memory_status,
    get_task_loop_status,
    get_verification_state,
    is_workflow_state_path,
    load_policy,
    load_reasoning_hotspot_entries,
    normalize_workspace_relative_path,
    parse_timestamp,
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
HOOK_REPEAT_WINDOW_MINUTES = 30
HOOK_PATH_KEYS = {"file_path", "path", "target_file", "relative_workspace_path"}


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


def gather_hook_target_paths(payload: Any, base_dir: Path) -> list[Path]:
    workspace_root = find_workspace_root(base_dir)
    paths: list[Path] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in HOOK_PATH_KEYS and isinstance(value, str):
                    candidate = Path(value).expanduser()
                    if candidate.is_absolute():
                        paths.append(candidate)
                    elif key == "relative_workspace_path":
                        paths.append(workspace_root / candidate)
                    else:
                        paths.append(base_dir / candidate)
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return paths


def load_hook_runtime_state(base_dir: Path) -> dict[str, Any]:
    path = get_state_paths(base_dir)["hook_state"]
    if not path.exists():
        return {"files": {}}

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": {}}

    if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), dict):
        return {"files": {}}
    return {"files": dict(parsed["files"])}


def persist_hook_runtime_state(base_dir: Path, state: dict[str, Any]) -> str:
    paths = ensure_state_files(base_dir)
    paths["runtime_dir"].mkdir(parents=True, exist_ok=True)
    hook_state_path = paths["hook_state"]
    hook_state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(hook_state_path)


def update_hook_runtime(base_dir: Path, payload: Any) -> dict[str, Any]:
    now = datetime.now().astimezone()
    cutoff = now - timedelta(minutes=HOOK_REPEAT_WINDOW_MINUTES)
    state = load_hook_runtime_state(base_dir)
    files_state = state.setdefault("files", {})

    touched_paths: list[str] = []
    repeated_edit_paths: list[str] = []
    for candidate in gather_hook_target_paths(payload, base_dir):
        relative = normalize_workspace_relative_path(base_dir, candidate)
        if not relative or is_workflow_state_path(relative):
            continue
        if relative not in touched_paths:
            touched_paths.append(relative)

    for relative in touched_paths:
        current = files_state.get(relative, {})
        if not isinstance(current, dict):
            current = {}
        events: list[str] = []
        for raw_timestamp in current.get("events", []):
            parsed = parse_timestamp(str(raw_timestamp))
            if parsed is not None and parsed >= cutoff:
                events.append(parsed.isoformat())
        events.append(now.isoformat())

        last_reminder = parse_timestamp(str(current.get("last_reminder_at") or ""))
        should_remind = len(events) >= 3 and (
            last_reminder is None or last_reminder < cutoff
        )
        if should_remind:
            repeated_edit_paths.append(relative)

        files_state[relative] = {
            "events": events,
            "last_reminder_at": now.isoformat() if should_remind else current.get("last_reminder_at"),
        }

    stale_paths: list[str] = []
    for relative, current in files_state.items():
        if not isinstance(current, dict):
            stale_paths.append(relative)
            continue
        events: list[str] = []
        for raw_timestamp in current.get("events", []):
            parsed = parse_timestamp(str(raw_timestamp))
            if parsed is not None and parsed >= cutoff:
                events.append(parsed.isoformat())
        last_reminder = parse_timestamp(str(current.get("last_reminder_at") or ""))
        last_reminder_value = (
            last_reminder.isoformat()
            if last_reminder is not None and last_reminder >= cutoff
            else None
        )
        if not events and last_reminder_value is None:
            stale_paths.append(relative)
            continue
        files_state[relative] = {
            "events": events,
            "last_reminder_at": last_reminder_value,
        }
    for relative in stale_paths:
        files_state.pop(relative, None)

    hook_state_path = persist_hook_runtime_state(base_dir, {"files": files_state})
    return {
        "path": hook_state_path,
        "touched_paths": touched_paths,
        "repeated_edit_paths": repeated_edit_paths,
    }


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


def build_reasoning_hotspots(
    *,
    changed_file_count: int,
    total_changed_lines: int,
    risk_level: str,
    task_loop_needed: bool,
    task_loop_stale: bool,
    task_loop_mode: str,
    task_stream_count: int,
    verification_state: str,
    uncovered_open_streams: list[str],
    repeated_edit_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    hotspots: list[dict[str, Any]] = []

    if task_loop_needed or task_loop_stale:
        if task_loop_stale:
            summary = "The live task loop looks stale or invalid relative to the current work."
        else:
            summary = (
                f"The change set grew to {changed_file_count} files / {total_changed_lines} lines "
                "and may have outgrown the initial plan."
            )
        hotspots.append(
            {
                "kind": "plan-drift",
                "summary": summary,
                "recommended_skills": ["execution-task-loop"],
            }
        )

    if risk_level in {"medium", "high"}:
        hotspots.append(
            {
                "kind": "risk-escalation",
                "summary": (
                    f"The active change set is currently classified as {risk_level}-risk and "
                    "deserves a fresh reasoning pass before more edits."
                ),
                "recommended_skills": ["policy-risk-check", "verify-change"],
            }
        )

    if repeated_edit_paths:
        hotspots.append(
            {
                "kind": "repeat-rewrite",
                "summary": (
                    "Repeated edits hit the same file(s) inside the last "
                    f"{HOOK_REPEAT_WINDOW_MINUTES} minutes: {', '.join(repeated_edit_paths)}."
                ),
                "recommended_skills": ["execution-task-loop", "verify-change"],
            }
        )

    if verification_state in {"missing", "stale", "invalid"}:
        hotspots.append(
            {
                "kind": "verification-gap",
                "summary": (
                    f"Verification is {verification_state}; the current work no longer has a "
                    "clean evidence trail."
                ),
                "recommended_skills": ["verify-change"],
            }
        )
    elif uncovered_open_streams:
        joined = ", ".join(uncovered_open_streams)
        hotspots.append(
            {
                "kind": "stream-coverage-gap",
                "summary": (
                    "Open task streams are missing current verification coverage: "
                    f"{joined}."
                ),
                "recommended_skills": ["verify-change", "execution-task-loop"],
            }
        )

    if task_loop_mode == "streams" and task_stream_count > 1:
        hotspots.append(
            {
                "kind": "multi-stream-coordination",
                "summary": (
                    f"The work is split across {task_stream_count} task streams, so the active "
                    "plan may need a quick coordination check before the next edit."
                ),
                "recommended_skills": ["execution-task-loop"],
            }
        )

    deduped: list[dict[str, Any]] = []
    seen_kinds: set[str] = set()
    for hotspot in hotspots:
        kind = str(hotspot.get("kind") or "")
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        deduped.append(hotspot)
    return deduped


def build_micro_reasoning_questions(task_loop_mode: str) -> list[str]:
    questions = [
        "What changed in my understanding since the initial plan?",
        "Which assumption, file, or stream is now the least certain?",
        "What is the smallest next edit or verification probe that would reduce uncertainty?",
    ]
    if task_loop_mode == "streams":
        questions.insert(
            2,
            "Do the current task streams still match reality, or should I split/merge/reorder them?",
        )
    else:
        questions.insert(
            2,
            "Does the current task loop still reflect the real next step, or does it need to be refreshed?",
        )
    return questions


def collect_reasoning_hotspot_skills(hotspots: list[dict[str, Any]]) -> list[str]:
    skills: list[str] = []
    for hotspot in hotspots:
        skills.extend(
            str(item)
            for item in hotspot.get("recommended_skills", [])
            if str(item).strip()
        )
    return dedupe_non_empty_strings(skills)


def enrich_reasoning_guidance(
    base_dir: Path,
    result: dict[str, Any],
    *,
    repeated_edit_paths: list[str] | None = None,
) -> dict[str, Any]:
    hotspots = build_reasoning_hotspots(
        changed_file_count=int(result.get("changed_file_count", 0) or 0),
        total_changed_lines=int(result.get("total_changed_lines", 0) or 0),
        risk_level=str(result.get("risk_level") or "low"),
        task_loop_needed=bool(result.get("task_loop_needed")),
        task_loop_stale=bool(result.get("task_loop_stale")),
        task_loop_mode=str(result.get("task_loop_mode") or "legacy"),
        task_stream_count=int(result.get("task_stream_count", 1) or 1),
        verification_state=str(result.get("verification_state") or "missing"),
        uncovered_open_streams=list(result.get("uncovered_open_streams", [])),
        repeated_edit_paths=repeated_edit_paths,
    )
    hotspot_history = get_reasoning_hotspot_state(base_dir)
    loaded_hotspots = load_reasoning_hotspot_entries(base_dir)
    recurring_hotspots = build_recurring_reasoning_hotspot_summary(
        loaded_hotspots["entries"],
        active_kinds=[str(item.get("kind") or "") for item in hotspots],
    )
    hotspot_skills = collect_reasoning_hotspot_skills(hotspots)
    recurring_skills = collect_reasoning_hotspot_skills(recurring_hotspots)

    result["reasoning_hotspots"] = hotspots
    result["micro_reasoning_recommended"] = bool(hotspots)
    result["recent_reasoning_hotspots"] = hotspot_history.get("recent_entries", [])
    result["recurring_reasoning_hotspots"] = recurring_hotspots
    result["micro_reasoning_escalation_recommended"] = bool(recurring_hotspots)
    result["escalated_recommended_skills"] = recurring_skills
    result["recommended_skills"] = dedupe_non_empty_strings(
        list(result.get("recommended_skills", [])) + hotspot_skills + recurring_skills
    )
    return result


def analyze_change_scope(base_dir: Path) -> dict[str, Any]:
    workspace_root = find_workspace_root(base_dir)
    policy_info = load_policy(base_dir)
    policy = policy_info["data"]
    memory = get_memory_status(base_dir)
    shared_memory = get_shared_memory_status(base_dir)
    memory_candidates = get_memory_candidate_state(base_dir)
    memory_sync = get_memory_sync_state(base_dir)
    reasoning_hotspots_state = get_reasoning_hotspot_state(base_dir)
    buglog = get_buglog_state(base_dir)
    task_loop = get_task_loop_status(base_dir, policy)
    verification = get_verification_state(base_dir, task_loop)
    uncovered_open_streams = list(verification.get("uncovered_open_streams", []))
    needs_state_repair = (
        memory["status"] == "invalid"
        or shared_memory["status"] == "invalid"
        or memory_candidates["status"] == "invalid"
        or memory_sync["status"] == "invalid"
        or reasoning_hotspots_state["status"] == "invalid"
        or buglog["status"] == "invalid"
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
            "reasoning_hotspots_status": reasoning_hotspots_state["status"],
            "reasoning_hotspots_entry_count": reasoning_hotspots_state["entry_count"],
            "buglog_status": buglog["status"],
            "buglog_entry_count": buglog["entry_count"],
            "memory_refresh_needed": False,
            "policy_status": policy_info["status"],
            "task_loop_status": task_loop["status"],
            "task_loop_stale": task_loop["stale"],
            "task_loop_mode": task_loop.get("mode", "legacy"),
            "task_stream_count": task_loop.get("stream_count", 1),
            "verification_state": verification["status"],
            "verification_coverage_gap": bool(uncovered_open_streams),
            "uncovered_open_streams": uncovered_open_streams,
            "verification_log_present": verification["entry_count"] > 0,
            "micro_reasoning_recommended": False,
            "reasoning_hotspots": [],
            "recent_reasoning_hotspots": reasoning_hotspots_state.get("recent_entries", []),
            "recurring_reasoning_hotspots": [],
            "micro_reasoning_questions": build_micro_reasoning_questions(
                task_loop.get("mode", "legacy")
            ),
            "micro_reasoning_escalation_recommended": False,
            "escalated_recommended_skills": [],
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
    result = {
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
        "reasoning_hotspots_status": reasoning_hotspots_state["status"],
        "reasoning_hotspots_entry_count": reasoning_hotspots_state["entry_count"],
        "buglog_status": buglog["status"],
        "buglog_entry_count": buglog["entry_count"],
        "memory_refresh_needed": memory_refresh_needed,
        "policy_status": policy_info["status"],
        "task_loop_status": task_loop["status"],
        "task_loop_stale": task_loop_stale,
        "task_loop_mode": task_loop.get("mode", "legacy"),
        "task_stream_count": task_loop.get("stream_count", 1),
        "verification_state": verification["status"],
        "verification_coverage_gap": bool(uncovered_open_streams),
        "uncovered_open_streams": uncovered_open_streams,
        "verification_log_present": verification["entry_count"] > 0,
        "micro_reasoning_recommended": False,
        "reasoning_hotspots": [],
        "recent_reasoning_hotspots": reasoning_hotspots_state.get("recent_entries", []),
        "recurring_reasoning_hotspots": [],
        "micro_reasoning_questions": build_micro_reasoning_questions(
            task_loop.get("mode", "legacy")
        ),
        "micro_reasoning_escalation_recommended": False,
        "escalated_recommended_skills": [],
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
    return enrich_reasoning_guidance(base_dir, result)


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
        "Verification log: {verification}\n"
        "Micro reasoning: {micro_reasoning}\n"
        "Hotspot escalation: {hotspot_escalation}"
    ).format(
        count=result["changed_file_count"],
        lines=result["total_changed_lines"],
        added=result["inserted_lines"],
        removed=result["deleted_lines"],
        categories=category_summary,
        risk=result["risk_level"],
        task_loop=result.get("task_loop_status", "unknown"),
        verification=result.get("verification_state", "unknown"),
        micro_reasoning="recommended" if result.get("micro_reasoning_recommended") else "not needed",
        hotspot_escalation=(
            "recommended"
            if result.get("micro_reasoning_escalation_recommended")
            else "not needed"
        ),
    )


def render_hook_message(result: dict[str, Any]) -> str:
    reminders: list[str] = []
    repeated_edit_paths = result.get("repeated_edit_paths", [])
    for relative in repeated_edit_paths:
        reminders.append(
            "[codex-coding-workflows] "
            f"`{relative}` was edited at least 3 times in the last {HOOK_REPEAT_WINDOW_MINUTES} "
            "minutes. If this is a bug fix, search bug memory before another rewrite, and "
            "append a confirmed buglog entry after a meaningful check or explicit user confirmation."
        )

    if result["status"] != "ok":
        return "\n".join(reminders)

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

    hotspots = build_reasoning_hotspots(
        changed_file_count=result["changed_file_count"],
        total_changed_lines=result["total_changed_lines"],
        risk_level=result["risk_level"],
        task_loop_needed=result["task_loop_needed"],
        task_loop_stale=result["task_loop_stale"],
        task_loop_mode=result.get("task_loop_mode", "legacy"),
        task_stream_count=int(result.get("task_stream_count", 1) or 1),
        verification_state=result.get("verification_state", "missing"),
        uncovered_open_streams=list(result.get("uncovered_open_streams", [])),
        repeated_edit_paths=repeated_edit_paths,
    )
    if hotspots:
        hotspot_lines = [f"- {hotspot['summary']}" for hotspot in hotspots[:3]]
        question_lines = [
            f"- {question}" for question in result.get("micro_reasoning_questions", [])
        ]
        reminders.append(
            "[codex-coding-workflows] Keep the initial plan as the anchor. "
            "Hotspot-triggered micro reasoning is recommended now; it supplements, not replaces, "
            "the upfront plan.\n"
            "Hotspots:\n"
            + "\n".join(hotspot_lines)
            + "\nBefore the next edit, run a short checkpoint:\n"
            + "\n".join(question_lines)
        )
    recurring_hotspots = result.get("recurring_reasoning_hotspots", [])
    if recurring_hotspots:
        recurring_lines = []
        for hotspot in recurring_hotspots[:2]:
            skills = ", ".join(
                f"`{skill}`" for skill in hotspot.get("recommended_skills", [])
            ) or "`execution-task-loop`"
            recurring_lines.append(f"- {hotspot['summary']} Escalate with: {skills}.")
        reminders.append(
            "[codex-coding-workflows] Recent hotspot history shows this is a recurring pattern.\n"
            + "\n".join(recurring_lines)
        )

    return "\n".join(reminders)


def build_hook_hotspot_related_items(result: dict[str, Any], hotspot: dict[str, Any]) -> list[str]:
    kind = str(hotspot.get("kind") or "")
    if kind == "repeat-rewrite":
        return [str(item) for item in result.get("repeated_edit_paths", []) if str(item).strip()]
    if kind == "stream-coverage-gap":
        return [str(item) for item in result.get("uncovered_open_streams", []) if str(item).strip()]
    return []


def persist_hook_reasoning_hotspots(base_dir: Path, result: dict[str, Any]) -> list[str]:
    if result.get("status") != "ok":
        return []

    written: list[str] = []
    for hotspot in result.get("reasoning_hotspots", []):
        payload = {
            "kind": hotspot["kind"],
            "summary": hotspot["summary"],
            "source": "post-tool-hook",
            "recommended_skills": hotspot.get("recommended_skills", []),
            "related_items": build_hook_hotspot_related_items(result, hotspot),
            "questions": result.get("micro_reasoning_questions", []),
            "risk_level": result.get("risk_level"),
            "task_loop_mode": result.get("task_loop_mode"),
            "task_stream_count": result.get("task_stream_count"),
        }
        path = append_reasoning_hotspot_entry(
            base_dir,
            payload,
            suppress_recent_duplicates=True,
        )
        if path is not None:
            written.append(str(path))
    return written


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
    if args.hook:
        result.update(update_hook_runtime(base_dir, payload))
        result = enrich_reasoning_guidance(
            base_dir,
            result,
            repeated_edit_paths=list(result.get("repeated_edit_paths", [])),
        )
        result["reasoning_hotspot_log_paths"] = persist_hook_reasoning_hotspots(base_dir, result)

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
