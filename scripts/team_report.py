#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from team_state import build_run_summary, get_team_run_paths, load_team_manifest
from workflow_state import find_workspace_root


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def extract_worker_output_excerpt(text: str, max_lines: int = 12) -> str:
    if not text.strip():
        return ""
    lines = text.strip().splitlines()
    excerpt = lines[:max_lines]
    if len(lines) > max_lines:
        excerpt.append("...")
    return "\n".join(excerpt)


def classify_run_blockers(summary: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if summary.get("validation_errors"):
        blockers.append("Team run state has validation errors.")
    if summary.get("missing_assignments"):
        blockers.append("One or more worker assignment artifacts are missing.")
    if summary.get("missing_outputs"):
        blockers.append("One or more completed workers are missing output artifacts.")

    worker_statuses = summary.get("worker_statuses", {})
    if any(status == "pending" for status in worker_statuses.values()):
        blockers.append("One or more workers have not started yet.")
    if any(status == "failed" for status in worker_statuses.values()):
        blockers.append("At least one worker failed.")
    if any(status == "running" for status in worker_statuses.values()):
        blockers.append("One or more workers are still running.")
    if summary.get("status") in {"failed", "cancelled"}:
        blockers.append(f"Run status is {summary.get('status')}.")
    return blockers


def build_next_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    worker_statuses = summary.get("worker_statuses", {})
    if any(status == "pending" for status in worker_statuses.values()):
        actions.append("Dispatch pending workers or mark them cancelled/partial before synthesis.")
    if any(status == "running" for status in worker_statuses.values()):
        actions.append("Wait for running workers or mark them partial/failed before synthesis.")
    if summary.get("missing_assignments"):
        actions.append("Restore missing worker assignments or review the run manually before relying on it.")
    if summary.get("missing_outputs"):
        actions.append("Regenerate or restore missing worker outputs before final synthesis.")
    if summary.get("validation_errors"):
        actions.append("Repair the team run state before relying on it for handoff or review.")
    if any(status == "failed" for status in worker_statuses.values()):
        actions.append("Decide whether to rerun failed workers or synthesize a partial result.")
    if not actions:
        actions.append("Team run looks consistent enough for conductor synthesis.")
    return actions


def build_team_report(
    base_dir: Path,
    run_id: str,
    include_output_excerpts: bool = False,
    max_excerpt_lines: int = 12,
) -> tuple[str, dict[str, Any]]:
    workspace_root = find_workspace_root(base_dir)
    summary = build_run_summary(base_dir, run_id)
    manifest = load_team_manifest(base_dir, run_id)
    paths = get_team_run_paths(base_dir, run_id)
    blockers = classify_run_blockers(summary)
    next_actions = build_next_actions(summary)

    lines = [
        "# Team Run Summary",
        "",
        "## Run",
        f"- Run id: `{run_id}`",
        f"- Workflow: `{summary.get('workflow') or 'unknown'}`",
        f"- Owner skill: `{summary.get('owner_skill') or 'unknown'}`",
        f"- Mode: `{summary.get('mode') or 'unknown'}`",
        f"- Status: `{summary.get('status') or 'unknown'}`",
        f"- Created: {summary.get('created_at') or 'unknown'}",
        f"- Updated: {summary.get('updated_at') or 'unknown'}",
        "",
        "## Goal",
        f"- {summary.get('goal') or 'No goal recorded.'}",
        "",
        "## Workers",
    ]

    workers = manifest.get("workers", [])
    if workers:
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            worker_id = worker.get("id", "unknown-worker")
            worker_status = worker.get("status", "unknown")
            worker_role = worker.get("role", "unknown-role")
            worker_summary = worker.get("summary") or "No summary recorded."
            lines.append(
                f"- `{worker_id}`: {worker_status} ({worker_role}) - {worker_summary}"
            )
    else:
        lines.append("- No workers recorded.")

    if include_output_excerpts and workers:
        lines.extend(["", "## Output Excerpts"])
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            worker_id = worker.get("id", "unknown-worker")
            output_path = paths["run_dir"] / str(worker.get("output_path", ""))
            excerpt = extract_worker_output_excerpt(
                read_text_if_exists(output_path),
                max_lines=max_excerpt_lines,
            )
            lines.append(f"### {worker_id}")
            lines.append("")
            lines.append("```text")
            lines.append(excerpt or "[no output]")
            lines.append("```")
            lines.append("")

    lines.extend(
        [
            "## Outcomes",
            f"- Worker count: {summary.get('worker_count', 0)}",
            f"- Events logged: {summary.get('events_count', 0)}",
            f"- Workers with live agent ids: {len(summary.get('agent_assigned_workers', []))}",
            f"- Completed workers: {summary.get('worker_counts', {}).get('completed', 0)}",
            f"- Failed workers: {summary.get('worker_counts', {}).get('failed', 0)}",
            f"- Running workers: {summary.get('worker_counts', {}).get('running', 0)}",
            "",
            "## Blockers",
        ]
    )
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- No blockers detected from team state alone.")

    lines.extend(["", "## Residual Uncertainty"])
    if summary.get("missing_outputs"):
        lines.extend(
            f"- Missing output for `{worker_id}`." for worker_id in summary["missing_outputs"]
        )
    elif summary.get("missing_assignments"):
        lines.extend(
            f"- Missing assignment for `{worker_id}`." for worker_id in summary["missing_assignments"]
        )
    elif blockers:
        lines.append("- See blockers above.")
    else:
        lines.append("- No obvious orchestration uncertainty recorded.")

    lines.extend(["", "## Recommended Next Actions"])
    lines.extend(f"- {action}" for action in next_actions)
    lines.append("")

    metadata = {
        "workspace_root": str(workspace_root),
        "run_id": run_id,
        "workflow": summary.get("workflow"),
        "owner_skill": summary.get("owner_skill"),
        "status": summary.get("status"),
        "worker_count": summary.get("worker_count", 0),
        "workers_with_agent_ids": len(summary.get("agent_assigned_workers", [])),
        "completed_workers": summary.get("worker_counts", {}).get("completed", 0),
        "failed_workers": summary.get("worker_counts", {}).get("failed", 0),
        "running_workers": summary.get("worker_counts", {}).get("running", 0),
        "missing_assignments": summary.get("missing_assignments", []),
        "missing_outputs": summary.get("missing_outputs", []),
        "blockers": blockers,
        "next_actions": next_actions,
    }
    return "\n".join(lines), metadata


def write_team_summary(base_dir: Path, run_id: str, markdown: str) -> Path:
    path = get_team_run_paths(base_dir, run_id)["summary"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return path


def render_text(metadata: dict[str, Any]) -> str:
    lines = [
        f"Run: {metadata['run_id']}",
        f"Status: {metadata.get('status') or 'unknown'}",
        f"Workers: {metadata.get('worker_count', 0)}",
    ]
    if metadata.get("blockers"):
        lines.append("Blockers:")
        lines.extend(f"- {blocker}" for blocker in metadata["blockers"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a team orchestration report.")
    parser.add_argument("--repo", type=str, default=".")
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--include-output-excerpts", action="store_true")
    parser.add_argument("--max-excerpt-lines", type=int, default=12)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = Path(args.repo).expanduser()

    markdown, metadata = build_team_report(
        base_dir=base_dir,
        run_id=args.run_id,
        include_output_excerpts=args.include_output_excerpts,
        max_excerpt_lines=args.max_excerpt_lines,
    )

    if args.write:
        write_team_summary(base_dir, args.run_id, markdown)

    if args.json:
        print(json.dumps(metadata, indent=2))
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
