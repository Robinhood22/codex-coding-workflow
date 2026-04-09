#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyze_change_scope import analyze_change_scope
from branch_readiness import summarize_branch
from team_state import list_runs
from verification_summary import build_verification_summary
from workflow_state import (
    ensure_state_files,
    find_git_root,
    find_workspace_root,
    get_default_section_lines,
    get_state_paths,
    inspect_workflow_state,
    parse_timestamp,
    parse_memory_sections,
)


REPORT_FILENAMES = {
    "review-ready": "review-ready-summary.md",
    "handoff": "handoff-summary.md",
}


def ensure_reports_dir(base_dir: Path) -> Path:
    paths = ensure_state_files(base_dir)
    reports_dir = paths["state_dir"] / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = reports_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")
    return reports_dir


def read_memory_sections(base_dir: Path) -> dict[str, list[str]]:
    memory_path = get_state_paths(base_dir)["memory"]
    if not memory_path.exists():
        return {}
    return parse_memory_sections(memory_path.read_text(encoding="utf-8"))


def summarize_branch_state(base_dir: Path) -> dict[str, Any] | None:
    repo_root = find_git_root(base_dir)
    if repo_root is None:
        return None
    return summarize_branch(repo_root)


def flatten_blockers(items: list[str]) -> list[str]:
    return [item for item in items if item]


def has_stream_coverage_gap(verification_summary: dict[str, Any]) -> bool:
    return any(
        not item.get("covered", False) and str(item.get("state") or "open") == "open"
        for item in verification_summary.get("stream_coverage", [])
    )


def filter_real_memory_lines(section_name: str, lines: list[str]) -> list[str]:
    defaults = set(get_default_section_lines(section_name))
    return [line for line in lines if line.strip() and line.strip() not in defaults]


def format_skill_list(skills: list[str]) -> str:
    rendered = [f"`{skill}`" for skill in skills if str(skill).strip()]
    return ", ".join(rendered)


def format_recent_hotspot_history(entries: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        timestamp = str(entry.get("timestamp") or "unknown-time")
        kind = str(entry.get("kind") or "unknown-kind")
        source = str(entry.get("source") or "unknown-source")
        summary = str(entry.get("summary") or "").strip()
        lines.append(f"- {timestamp} | `{kind}` via `{source}`: {summary}")
    return lines


def summarize_latest_team_run(base_dir: Path) -> dict[str, Any] | None:
    runs = list_runs(base_dir)
    if not runs:
        return None

    def sort_key(run: dict[str, Any]) -> str:
        updated_at = parse_timestamp(str(run.get("updated_at") or ""))
        return updated_at.isoformat() if updated_at is not None else ""

    latest = sorted(runs, key=sort_key, reverse=True)[0]
    blockers: list[str] = []
    if latest.get("validation_errors"):
        blockers.append("Team run state has validation errors.")
    if latest.get("missing_assignments"):
        blockers.append("One or more worker assignments are missing.")
    if latest.get("missing_outputs"):
        blockers.append("Completed workers are missing output artifacts.")
    worker_statuses = latest.get("worker_statuses", {})
    if any(status == "pending" for status in worker_statuses.values()):
        blockers.append("One or more workers are still pending.")
    if any(status == "running" for status in worker_statuses.values()):
        blockers.append("One or more workers are still running.")
    if latest.get("status") in {"failed", "cancelled", "partial"}:
        blockers.append(f"Run status is {latest.get('status')}.")

    return {
        "run_id": latest["run_id"],
        "status": latest.get("status"),
        "updated_at": latest.get("updated_at"),
        "workflow": latest.get("workflow"),
        "worker_count": latest.get("worker_count", 0),
        "worker_counts": latest.get("worker_counts", {}),
        "agent_assigned_workers": latest.get("agent_assigned_workers", []),
        "missing_assignments": latest.get("missing_assignments", []),
        "blockers": blockers,
    }


def build_next_actions(
    change_scope: dict[str, Any],
    verification_summary: dict[str, Any],
    branch_summary: dict[str, Any] | None,
    latest_team_run: dict[str, Any] | None,
    state: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if change_scope.get("micro_reasoning_recommended"):
        actions.append(
            "Run a short hotspot-triggered micro-reasoning checkpoint while keeping the initial plan as the anchor."
        )
    if change_scope.get("micro_reasoning_escalation_recommended"):
        escalated_skills = format_skill_list(change_scope.get("escalated_recommended_skills", []))
        if escalated_skills:
            actions.append(f"Escalate recurring hotspot patterns with {escalated_skills}.")
        else:
            actions.append("Escalate recurring hotspot patterns beyond a quick checkpoint.")
    if change_scope.get("state_repair_needed"):
        actions.append("Run workflow-state-repair before relying on repo-local workflow state.")
    if state.get("shared_memory", {}).get("status") == "invalid":
        actions.append("Repair shared memory before relying on shared durable context.")
    if state.get("memory_candidates", {}).get("pending_count", 0) > 0:
        actions.append("Run project-memory-sync auto-refresh to promote queued memory candidates.")
    if state.get("memory_sync", {}).get("status") == "invalid":
        actions.append("Repair the memory sync log before trusting automatic/shared memory state.")
    if state.get("reasoning_hotspots", {}).get("status") == "invalid":
        actions.append("Repair the reasoning hotspot log before relying on durable micro-reasoning recall.")
    if state.get("buglog", {}).get("status") == "invalid":
        actions.append("Repair bug memory before relying on historical bug-fix recall.")
    if change_scope.get("task_loop_status") in {"missing", "stale", "invalid"}:
        actions.append("Refresh the active task loop so it matches the current implementation.")
    if verification_summary.get("status") in {"missing", "stale", "invalid"}:
        actions.append("Run verify-change or refresh verification evidence before review or handoff.")
    elif has_stream_coverage_gap(verification_summary):
        actions.append("Refresh verification so every open task stream has current coverage.")
    if branch_summary and branch_summary.get("upstream") is None:
        actions.append("Set an upstream branch before treating this as ready to land.")
    if latest_team_run and latest_team_run.get("blockers"):
        actions.append(
            f"Resolve blockers in team run `{latest_team_run['run_id']}` before treating the orchestration state as complete."
        )
    if not actions:
        actions.append("No immediate workflow blockers detected from plugin state alone.")
    return actions


def build_review_ready_report(base_dir: Path) -> tuple[str, dict[str, Any]]:
    workspace_root = find_workspace_root(base_dir)
    state = inspect_workflow_state(base_dir)
    change_scope = analyze_change_scope(base_dir)
    verification = build_verification_summary(base_dir)
    branch_summary = summarize_branch_state(base_dir)
    latest_team_run = summarize_latest_team_run(base_dir)
    next_actions = build_next_actions(
        change_scope,
        verification,
        branch_summary,
        latest_team_run,
        state,
    )

    lines = [
        "# Review-Ready Summary",
        "",
        f"Workspace: `{workspace_root}`",
        "",
        "## Status",
        f"- Risk level: {change_scope.get('risk_level', 'unknown')}",
        f"- Task loop: {state['task_loop']['status']}",
        f"- Task loop mode: {state['task_loop'].get('mode', 'legacy')}",
        f"- Task streams: {state['task_loop'].get('stream_count', 1)}",
        f"- Verification: {verification['status']}",
        f"- Memory: {state['memory']['status']}",
        f"- Shared memory: {state['shared_memory']['status']}",
        f"- Memory candidates: {state['memory_candidates']['pending_count']}",
        f"- Reasoning hotspots: {state['reasoning_hotspots']['status']} ({state['reasoning_hotspots']['entry_count']})",
        f"- Policy: {state['policy']['status']}",
        "",
        "## Change Scope",
        f"- Changed files: {change_scope.get('changed_file_count', 0)}",
        f"- Changed lines: {change_scope.get('total_changed_lines', 0)}",
    ]

    changed_files = change_scope.get("changed_files", [])
    if changed_files:
        lines.append("- Files:")
        lines.extend([f"  - `{path}`" for path in changed_files])

    lines.extend(
        [
            "",
            "## Verification",
            f"- Latest verdict: {verification.get('latest_verdict') or 'none'}",
            f"- Latest timestamp: {verification.get('latest_timestamp') or 'none'}",
            f"- Valid entries: {verification.get('entry_count', 0)}",
            f"- Invalid lines: {verification.get('invalid_lines', 0)}",
        ]
    )
    if verification.get("stream_coverage"):
        lines.append("- Stream coverage:")
        lines.extend(
            [
                f"  - {item['id']}: {'covered' if item['covered'] else 'missing'}"
                for item in verification["stream_coverage"]
            ]
        )
    if verification.get("blockers"):
        lines.append("- Verification blockers:")
        lines.extend([f"  - {item}" for item in verification["blockers"]])

    if change_scope.get("reasoning_hotspots"):
        lines.extend(["", "## Active Hotspots"])
        lines.extend(
            [f"- {item['summary']}" for item in change_scope["reasoning_hotspots"]]
        )
    if change_scope.get("recurring_reasoning_hotspots"):
        lines.extend(["", "## Recurring Hotspots"])
        for item in change_scope["recurring_reasoning_hotspots"]:
            skill_text = format_skill_list(item.get("recommended_skills", []))
            if skill_text:
                lines.append(f"- {item['summary']} Escalate with: {skill_text}.")
            else:
                lines.append(f"- {item['summary']}")
    recent_hotspots = state["reasoning_hotspots"].get("recent_entries", [])
    if recent_hotspots:
        lines.extend(["", "## Recent Hotspot History"])
        lines.extend(format_recent_hotspot_history(recent_hotspots))

    lines.extend(["", "## Latest Team Run"])
    if latest_team_run:
        lines.append(f"- Run id: `{latest_team_run['run_id']}`")
        lines.append(f"- Workflow: `{latest_team_run.get('workflow') or 'unknown'}`")
        lines.append(f"- Status: {latest_team_run.get('status') or 'unknown'}")
        lines.append(f"- Updated: {latest_team_run.get('updated_at') or 'unknown'}")
        lines.append(
            f"- Workers with live agent ids: {len(latest_team_run.get('agent_assigned_workers', []))}"
        )
        if latest_team_run.get("blockers"):
            lines.append("- Team run blockers:")
            lines.extend([f"  - {item}" for item in latest_team_run["blockers"]])
    else:
        lines.append("- No team runs recorded.")

    lines.extend(["", "## Blockers"])
    blockers = list(branch_summary["blockers"]) if branch_summary else ["No git repository detected."]
    for blocker in verification.get("blockers", []):
        if blocker not in blockers:
            blockers.append(blocker)
    lines.extend([f"- {blocker}" for blocker in blockers])

    lines.extend(["", "## Recommended Next Actions"])
    lines.extend([f"- {action}" for action in next_actions])
    lines.append("")

    report = "\n".join(lines)
    metadata = {
        "mode": "review-ready",
        "workspace_root": str(workspace_root),
        "state": state,
        "change_scope": change_scope,
        "verification": verification,
        "branch_summary": branch_summary,
        "latest_team_run": latest_team_run,
        "next_actions": next_actions,
    }
    return report, metadata


def build_handoff_report(base_dir: Path) -> tuple[str, dict[str, Any]]:
    workspace_root = find_workspace_root(base_dir)
    state = inspect_workflow_state(base_dir)
    change_scope = analyze_change_scope(base_dir)
    verification = build_verification_summary(base_dir)
    branch_summary = summarize_branch_state(base_dir)
    memory_sections = read_memory_sections(base_dir)
    latest_team_run = summarize_latest_team_run(base_dir)
    next_actions = build_next_actions(
        change_scope,
        verification,
        branch_summary,
        latest_team_run,
        state,
    )

    stable_facts = memory_sections.get("Stable Facts", [])
    constraints = memory_sections.get("Constraints", [])
    open_questions = memory_sections.get("Open Questions", [])
    do_not_repeat = filter_real_memory_lines(
        "Do-Not-Repeat",
        memory_sections.get("Do-Not-Repeat", []),
    )
    decision_log = filter_real_memory_lines(
        "Decision Log",
        memory_sections.get("Decision Log", []),
    )

    lines = [
        "# Handoff Summary",
        "",
        f"Workspace: `{workspace_root}`",
        "",
        "## What Changed",
        f"- Risk level: {change_scope.get('risk_level', 'unknown')}",
        f"- Changed files: {change_scope.get('changed_file_count', 0)}",
        f"- Task loop mode: {state['task_loop'].get('mode', 'legacy')}",
        f"- Task streams: {state['task_loop'].get('stream_count', 1)}",
    ]
    changed_files = change_scope.get("changed_files", [])
    if changed_files:
        lines.extend([f"- `{path}`" for path in changed_files])

    lines.extend(
        [
            "",
            "## What Is Verified",
            f"- Verification state: {verification['status']}",
            f"- Latest verdict: {verification.get('latest_verdict') or 'none'}",
            f"- Latest timestamp: {verification.get('latest_timestamp') or 'none'}",
            f"- Shared memory: {state['shared_memory']['status']}",
            f"- Memory candidates: {state['memory_candidates']['pending_count']}",
            f"- Reasoning hotspots: {state['reasoning_hotspots']['status']} ({state['reasoning_hotspots']['entry_count']})",
        ]
    )
    if verification.get("stream_coverage"):
        lines.append("- Stream coverage:")
        lines.extend(
            [
                f"  - {item['id']}: {'covered' if item['covered'] else 'missing'}"
                for item in verification["stream_coverage"]
            ]
        )
    if verification.get("blockers"):
        lines.append("- Verification blockers:")
        lines.extend([f"  - {item}" for item in verification["blockers"]])

    if change_scope.get("reasoning_hotspots"):
        lines.extend(["", "## Active Hotspots"])
        lines.extend(
            [f"- {item['summary']}" for item in change_scope["reasoning_hotspots"]]
        )
    if change_scope.get("recurring_reasoning_hotspots"):
        lines.extend(["", "## Recurring Hotspots"])
        for item in change_scope["recurring_reasoning_hotspots"]:
            skill_text = format_skill_list(item.get("recommended_skills", []))
            if skill_text:
                lines.append(f"- {item['summary']} Escalate with: {skill_text}.")
            else:
                lines.append(f"- {item['summary']}")
    recent_hotspots = state["reasoning_hotspots"].get("recent_entries", [])
    if recent_hotspots:
        lines.extend(["", "## Recent Hotspot History"])
        lines.extend(format_recent_hotspot_history(recent_hotspots))

    lines.extend(["", "## Latest Team Run"])
    if latest_team_run:
        lines.append(f"- Run id: `{latest_team_run['run_id']}`")
        lines.append(f"- Status: {latest_team_run.get('status') or 'unknown'}")
        lines.append(
            f"- Workers with live agent ids: {len(latest_team_run.get('agent_assigned_workers', []))}"
        )
        if latest_team_run.get("blockers"):
            lines.append("- Team run blockers:")
            lines.extend([f"  - {item}" for item in latest_team_run["blockers"]])
    else:
        lines.append("- No team runs recorded.")

    lines.extend(["", "## What Is Still Open"])
    open_items = flatten_blockers(
        (branch_summary["blockers"] if branch_summary else [])
        + verification.get("blockers", [])
        + state["task_loop"].get("reasons", [])
        + (latest_team_run["blockers"] if latest_team_run else [])
    )
    if open_items:
        lines.extend([f"- {item}" for item in open_items])
    else:
        lines.append("- No workflow blockers detected from plugin state alone.")

    lines.extend(["", "## Stable Facts"])
    lines.extend(stable_facts or ["- No stable facts recorded."])

    lines.extend(["", "## Constraints"])
    lines.extend(constraints or ["- No durable constraints recorded."])

    if do_not_repeat:
        lines.extend(["", "## Do-Not-Repeat"])
        lines.extend(do_not_repeat)

    if decision_log:
        lines.extend(["", "## Recent Decision Log"])
        lines.extend(decision_log[-3:])

    lines.extend(["", "## Open Questions"])
    lines.extend(open_questions or ["- None."])

    lines.extend(["", "## Next Actions For The Next Person"])
    lines.extend([f"- {action}" for action in next_actions])
    lines.append("")

    report = "\n".join(lines)
    metadata = {
        "mode": "handoff",
        "workspace_root": str(workspace_root),
        "state": state,
        "change_scope": change_scope,
        "verification": verification,
        "branch_summary": branch_summary,
        "memory_sections": memory_sections,
        "latest_team_run": latest_team_run,
        "next_actions": next_actions,
    }
    return report, metadata


def write_report(base_dir: Path, mode: str) -> dict[str, Any]:
    reports_dir = ensure_reports_dir(base_dir)
    if mode == "review-ready":
        report_text, metadata = build_review_ready_report(base_dir)
    elif mode == "handoff":
        report_text, metadata = build_handoff_report(base_dir)
    else:
        raise SystemExit(f"Unsupported mode: {mode}")

    output_path = reports_dir / REPORT_FILENAMES[mode]
    output_path.write_text(report_text, encoding="utf-8")
    return {
        "mode": mode,
        "path": str(output_path),
        "workspace_root": metadata["workspace_root"],
        "metadata": metadata,
    }


def render_text(result: dict[str, Any]) -> str:
    lines = [
        f"Report mode: {result['mode']}",
        f"Report path: {result['path']}",
    ]
    next_actions = result["metadata"].get("next_actions", [])
    if next_actions:
        lines.append("Next actions:")
        lines.extend([f"- {action}" for action in next_actions])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate review-ready or handoff workflow reports.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument(
        "--mode",
        type=str,
        required=True,
        choices=sorted(REPORT_FILENAMES.keys()),
        help="Report mode to generate.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    result = write_report(Path(args.repo).expanduser(), args.mode)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
