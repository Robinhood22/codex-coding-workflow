#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any

from team_state import build_worker_summary


def quote_shell(value: str) -> str:
    return shlex.quote(value)


def recommended_reasoning_effort(role: str | None) -> str:
    if role in {"semantic-tracer", "regression-hunter", "skeptic"}:
        return "high"
    return "medium"


def build_worker_prompt(packet: dict[str, Any]) -> str:
    worker = packet["worker"]
    assignment_text = packet["assignment_text"].strip()
    execution = packet.get("execution", {})
    suggested_workdir = packet.get("suggested_workdir")

    lines = [
        f"You are `{worker['id']}` in a conductor-owned team run.",
        "",
        "Run Context",
        f"- Run id: `{packet['run_id']}`",
        f"- Workflow: `{packet.get('workflow') or 'unknown'}`",
        f"- Owner skill: `{packet.get('owner_skill') or 'unknown'}`",
        f"- Run status at dispatch: `{packet.get('run_status') or 'unknown'}`",
        f"- Role: `{worker.get('role') or 'unknown'}`",
        "",
        "Rules",
        "- You are not alone in the codebase; do not revert or overwrite the work of others.",
        "- Do not mutate `.codex-workflows/`; the conductor owns workflow state.",
        "- Stay within your bounded assignment and do not silently broaden scope.",
        "- Return concrete evidence and call out uncertainty explicitly.",
        "- Do not claim proof or consensus just because other workers may agree.",
    ]
    if suggested_workdir:
        lines.extend(
            [
                "",
                "Execution Context",
                f"- Isolated working directory: `{suggested_workdir}`",
                f"- Workflow state repo root: `{packet['workspace_root']}`",
                "- Run task code edits and shell commands inside the isolated working directory.",
                "- The conductor-owned state commands still target the workflow state repo root via `--repo`.",
            ]
        )
        if execution.get("worktree_branch"):
            lines.append(f"- Worktree branch: `{execution['worktree_branch']}`")
        if execution.get("base_branch"):
            lines.append(f"- Base branch: `{execution['base_branch']}`")

    lines.extend(
        [
            "",
            "Return Format",
            "- What I inspected",
            "- Concrete findings or returned work",
            "- Unresolved questions",
            "- Confidence",
            "",
            "Assignment",
            assignment_text,
            "",
            "Final Reminder",
            "Return only your worker result in markdown. The conductor owns synthesis.",
            "",
        ]
    )
    return "\n".join(lines)


def build_worker_packet(base_dir: Path, run_id: str, worker_id: str) -> dict[str, Any]:
    summary = build_worker_summary(
        base_dir=base_dir,
        run_id=run_id,
        worker_id=worker_id,
        include_assignment=True,
    )
    worker = summary["worker"]
    assignment_text = str(summary.get("assignment_text", "")).strip()
    if not assignment_text:
        raise SystemExit(
            f"Worker {worker_id!r} does not have an assignment file or assignment text."
        )

    repo_root = quote_shell(summary["workspace_root"])
    run_id_quoted = quote_shell(str(summary["run_id"]))
    worker_id_quoted = quote_shell(str(worker["id"]))
    execution = summary.get("execution", {})
    suggested_workdir = (
        str(execution.get("worktree_path"))
        if execution.get("execution_mode") == "worktree" and execution.get("worktree_path")
        else None
    )

    status_update_command = (
        f"python3 ./scripts/team_state.py --repo {repo_root} --run-id {run_id_quoted} "
        f"--set-worker-status --worker-id {worker_id_quoted} --status running "
        f"--agent-id <agent-id>"
    )
    output_record_command = (
        f"python3 ./scripts/team_state.py --repo {repo_root} --run-id {run_id_quoted} "
        f"--write-output --worker-id {worker_id_quoted} --output-file <worker-output-file> "
        f'--summary "<one-line summary>" --confidence <0.0-1.0>'
    )

    packet = {
        "run_id": summary["run_id"],
        "workspace_root": summary["workspace_root"],
        "workflow": summary.get("workflow"),
        "owner_skill": summary.get("owner_skill"),
        "goal": summary.get("goal"),
        "run_status": summary.get("run_status"),
        "execution": execution,
        "suggested_workdir": suggested_workdir,
        "worker": worker,
        "assignment_text": assignment_text,
        "recommended_agent_type": "default",
        "recommended_reasoning_effort": recommended_reasoning_effort(worker.get("role")),
        "prompt": build_worker_prompt(
            {
                **summary,
                "execution": execution,
                "suggested_workdir": suggested_workdir,
            }
        ),
        "status_update_command": status_update_command,
        "output_record_command": output_record_command,
        "completion_checklist": [
            "Spawn the worker with the generated prompt.",
            *(
                [
                    "Use the suggested_workdir as the authoritative checkout for code edits and shell commands."
                ]
                if suggested_workdir
                else []
            ),
            "Record the returned agent id with team_state.py once the worker starts.",
            "Persist the worker result with team_state.py --write-output when the worker finishes.",
        ],
    }
    return packet


def render_text(packet: dict[str, Any]) -> str:
    worker = packet["worker"]
    lines = [
        f"Worker packet: {worker['id']}",
        f"Run: {packet['run_id']}",
        f"Workflow: {packet.get('workflow') or 'unknown'}",
        f"Recommended agent type: {packet['recommended_agent_type']}",
        f"Recommended reasoning effort: {packet['recommended_reasoning_effort']}",
    ]
    if packet.get("suggested_workdir"):
        lines.append(f"Suggested workdir: {packet['suggested_workdir']}")
    lines.extend(
        [
            "",
            "Status update command:",
            packet["status_update_command"],
            "",
            "Output record command:",
            packet["output_record_command"],
            "",
            "Prompt:",
            packet["prompt"],
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a ready-to-send subagent worker packet for a team run."
    )
    parser.add_argument("--repo", type=str, default=".")
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--worker-id", type=str, required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = Path(args.repo).expanduser()

    packet = build_worker_packet(
        base_dir=base_dir,
        run_id=args.run_id,
        worker_id=args.worker_id,
    )

    if args.json:
        print(json.dumps(packet, indent=2))
    else:
        print(render_text(packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
