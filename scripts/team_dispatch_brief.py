#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from team_state import build_run_summary, get_team_run_paths
from team_worker_packet import build_worker_packet


def select_worker_ids(
    run_summary: dict[str, Any],
    requested_workers: list[str] | None,
    include_all: bool,
) -> list[str]:
    worker_details = run_summary.get("worker_details", {})
    if requested_workers:
        return requested_workers
    if include_all:
        return sorted(worker_details.keys())
    return [
        worker_id
        for worker_id, details in sorted(worker_details.items())
        if details.get("status") in {"pending", "running"}
    ]


def build_dispatch_brief(
    base_dir: Path,
    run_id: str,
    worker_ids: list[str] | None = None,
    include_all: bool = False,
) -> tuple[str, dict[str, Any]]:
    run_summary = build_run_summary(base_dir, run_id)
    selected_worker_ids = select_worker_ids(run_summary, worker_ids, include_all)
    packets = [
        build_worker_packet(base_dir=base_dir, run_id=run_id, worker_id=worker_id)
        for worker_id in selected_worker_ids
    ]

    lines = [
        "# Team Dispatch Brief",
        "",
        "## Run",
        f"- Run id: `{run_id}`",
        f"- Workflow: `{run_summary.get('workflow') or 'unknown'}`",
        f"- Owner skill: `{run_summary.get('owner_skill') or 'unknown'}`",
        f"- Status: `{run_summary.get('status') or 'unknown'}`",
        f"- Worker count: {run_summary.get('worker_count', 0)}",
        "",
        "## Conductor Loop",
        "- Spawn each selected worker with its packet `prompt`.",
        "- Record the returned live agent id with the packet `status_update_command`.",
        "- Persist the finished worker result with the packet `output_record_command`.",
        "- Move the run to `synthesizing`, write the team summary, then close the run.",
        "",
        "## Selected Workers",
    ]

    execution = run_summary.get("execution", {})
    if execution.get("exists"):
        lines[8:8] = [
            "## Execution",
            f"- Mode: `{execution.get('execution_mode') or 'unknown'}`",
            f"- Status: `{execution.get('execution_status') or execution.get('status') or 'unknown'}`",
            f"- Workflow state repo root: `{run_summary.get('workspace_root') or 'unknown'}`",
            *(
                [f"- Isolated worktree path: `{execution['worktree_path']}`"]
                if execution.get("worktree_path")
                else []
            ),
            *(
                [f"- Worktree branch: `{execution['worktree_branch']}`"]
                if execution.get("worktree_branch")
                else []
            ),
            "",
        ]

    if not packets:
        lines.append("- No workers selected.")

    for packet in packets:
        worker = packet["worker"]
        lines.extend(
            [
                f"- `{worker['id']}` ({worker.get('role') or 'unknown'}) - {worker.get('status') or 'unknown'}",
                "",
                f"### {worker['id']}",
                "",
                f"- Recommended agent type: `{packet['recommended_agent_type']}`",
                f"- Recommended reasoning effort: `{packet['recommended_reasoning_effort']}`",
                *(
                    [f"- Suggested workdir: `{packet['suggested_workdir']}`"]
                    if packet.get("suggested_workdir")
                    else []
                ),
                "",
                "Status update command:",
                "```text",
                packet["status_update_command"],
                "```",
                "",
                "Output record command:",
                "```text",
                packet["output_record_command"],
                "```",
                "",
                "Prompt:",
                "```text",
                packet["prompt"].rstrip(),
                "```",
                "",
            ]
        )

    metadata = {
        "run_id": run_id,
        "run_summary": run_summary,
        "selected_workers": selected_worker_ids,
        "packets": packets,
    }
    return "\n".join(lines).rstrip() + "\n", metadata


def write_dispatch_brief(base_dir: Path, run_id: str, markdown: str) -> Path:
    output_path = get_team_run_paths(base_dir, run_id)["run_dir"] / "dispatch-brief.md"
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a conductor-side dispatch brief for one team run."
    )
    parser.add_argument("--repo", type=str, default=".")
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument(
        "--worker-id",
        action="append",
        default=None,
        help="Restrict the brief to one or more specific worker ids.",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Include completed workers too. By default only pending/running workers are selected.",
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = Path(args.repo).expanduser()

    markdown, metadata = build_dispatch_brief(
        base_dir=base_dir,
        run_id=args.run_id,
        worker_ids=args.worker_id,
        include_all=args.include_all,
    )

    if args.write:
        metadata["path"] = str(write_dispatch_brief(base_dir, args.run_id, markdown))

    if args.json:
        print(json.dumps(metadata, indent=2))
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
