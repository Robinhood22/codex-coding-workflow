#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from team_state import list_runs
from workflow_state import (
    append_memory_candidate,
    append_memory_entry,
    append_memory_sync_entry,
    append_verification_entry,
    ensure_state_files,
    inspect_workflow_state,
    load_policy,
    mirror_shared_memory_into_local,
    parse_timestamp,
    promote_memory_candidates,
    update_task_loop,
)


def load_text_argument(inline_text: str | None, file_path: str | None) -> str:
    if inline_text:
        return inline_text
    if file_path:
        return Path(file_path).expanduser().read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def render_text(summary: dict[str, Any]) -> str:
    lines = [
        f"Workspace root: {summary['workspace_root']}",
        f"State dir: {summary['state_dir']}",
        f"Memory: {summary['memory']['status']}",
        f"Shared memory: {summary['shared_memory']['status']}",
        f"Memory candidates: {summary['memory_candidates']['status']} ({summary['memory_candidates']['pending_count']} pending)",
        f"Memory sync: {summary['memory_sync']['status']}",
        f"Bug log: {summary['buglog']['status']} ({summary['buglog']['entry_count']} entries)",
        f"Policy: {summary['policy']['status']}",
        f"Task loop: {summary['task_loop']['status']}",
        f"Verification: {summary['verification']['status']}",
    ]

    errors = summary["policy"]["errors"]
    if errors:
        lines.append("Policy notes:")
        lines.extend(f"- {error}" for error in errors)

    latest_team_run = summary.get("latest_team_run")
    if latest_team_run:
        lines.append(
            f"Latest team run: {latest_team_run['run_id']} ({latest_team_run.get('status') or 'unknown'})"
        )

    auto_refresh = summary.get("auto_refresh")
    if auto_refresh:
        lines.append(
            "Auto refresh: "
            f"{auto_refresh['promoted_local']} local, "
            f"{auto_refresh['promoted_shared']} shared, "
            f"{auto_refresh['mirrored_shared_count']} mirrored"
        )
        if auto_refresh.get("blocked_shared"):
            lines.append(
                f"Blocked shared entries: {len(auto_refresh['blocked_shared'])}"
            )

    return "\n".join(lines)


def summarize_latest_team_run(base_dir: Path) -> dict[str, Any] | None:
    runs = list_runs(base_dir)
    if not runs:
        return None

    def sort_key(run: dict[str, Any]) -> str:
        updated_at = parse_timestamp(str(run.get("updated_at") or ""))
        return updated_at.isoformat() if updated_at is not None else ""

    latest = sorted(runs, key=sort_key, reverse=True)[0]
    return {
        "run_id": latest["run_id"],
        "status": latest.get("status"),
        "updated_at": latest.get("updated_at"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage repo-local workflow state.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument("--init", action="store_true", help="Create missing workflow state files.")
    parser.add_argument("--show", action="store_true", help="Show the current workflow state summary.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--append-memory", type=str, default=None, help="Append a durable memory entry.")
    parser.add_argument(
        "--append-memory-section",
        type=str,
        default=None,
        help="Section to target when using --append-memory.",
    )
    parser.add_argument(
        "--append-memory-candidate",
        type=str,
        default=None,
        help="Queue a durable memory candidate for later auto-refresh promotion.",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default="local",
        choices=["local", "shared"],
        help="Memory scope for append or candidate operations.",
    )
    parser.add_argument(
        "--section",
        type=str,
        default="Stable Facts",
        help="Memory section for append or candidate operations.",
    )
    parser.add_argument(
        "--candidate-source",
        type=str,
        default="manual",
        help="Source label for queued memory candidates.",
    )
    parser.add_argument(
        "--auto-refresh",
        action="store_true",
        help="Promote queued memory candidates and mirror shared memory into local memory.",
    )
    parser.add_argument("--set-task-loop", type=str, default=None, help="Replace the active task loop with inline text.")
    parser.add_argument("--task-loop-file", type=str, default=None, help="Load task loop content from a file.")
    parser.add_argument(
        "--append-verification-json",
        type=str,
        default=None,
        help="Append one verification entry encoded as a JSON object.",
    )
    parser.add_argument(
        "--append-verification-file",
        type=str,
        default=None,
        help="Append one verification entry from a JSON file.",
    )
    args = parser.parse_args()

    base_dir = Path(args.repo).expanduser()

    if args.init:
        ensure_state_files(base_dir)

    if args.append_memory:
        append_memory_entry(
            base_dir,
            args.append_memory,
            section=args.append_memory_section or args.section,
            scope=args.scope,
        )
        if args.scope == "shared":
            policy = load_policy(base_dir)["data"]
            mirrored = {"mirrored_count": 0}
            if policy["memory"].get("mirror_shared_into_local", False):
                mirrored = mirror_shared_memory_into_local(base_dir)
            append_memory_sync_entry(
                base_dir,
                {
                    "action": "manual_shared_append",
                    "summary": {
                        "section": args.append_memory_section or args.section,
                        "mirrored_shared_count": mirrored["mirrored_count"],
                    },
                },
            )

    if args.append_memory_candidate:
        append_memory_candidate(
            base_dir,
            {
                "scope": args.scope,
                "section": args.section,
                "text": args.append_memory_candidate,
                "source": args.candidate_source,
            },
        )

    task_loop_text = (
        load_text_argument(args.set_task_loop, args.task_loop_file)
        if args.set_task_loop or args.task_loop_file
        else ""
    )
    if task_loop_text.strip():
        update_task_loop(base_dir, task_loop_text)

    verification_payload = (
        load_text_argument(
            args.append_verification_json,
            args.append_verification_file,
        )
        if args.append_verification_json or args.append_verification_file
        else ""
    )
    if verification_payload.strip():
        try:
            parsed = json.loads(verification_payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid verification JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SystemExit("Verification payload must decode to a JSON object.")
        append_verification_entry(base_dir, parsed)

    auto_refresh = promote_memory_candidates(base_dir) if args.auto_refresh else None

    summary = inspect_workflow_state(base_dir)
    summary["latest_team_run"] = summarize_latest_team_run(base_dir)
    if auto_refresh is not None:
        summary["auto_refresh"] = auto_refresh
    if args.json:
        print(json.dumps(summary, indent=2))
    elif (
        args.show
        or args.init
        or args.append_memory
        or args.append_memory_candidate
        or args.auto_refresh
        or task_loop_text.strip()
        or verification_payload.strip()
    ):
        print(render_text(summary))
    else:
        print(
            "No operation requested. Use --show, --init, --append-memory, "
            "--append-memory-candidate, --auto-refresh, --set-task-loop, "
            "--append-verification-json, or --append-memory-section."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
