#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from workflow_state import (
    append_memory_fact,
    append_verification_entry,
    ensure_state_files,
    inspect_workflow_state,
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
        f"Policy: {summary['policy']['status']}",
        f"Task loop: {summary['task_loop']['status']}",
        f"Verification: {summary['verification']['status']}",
    ]

    errors = summary["policy"]["errors"]
    if errors:
        lines.append("Policy notes:")
        lines.extend(f"- {error}" for error in errors)

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage repo-local workflow state.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument("--init", action="store_true", help="Create missing workflow state files.")
    parser.add_argument("--show", action="store_true", help="Show the current workflow state summary.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--append-memory", type=str, default=None, help="Append a stable fact to memory.md.")
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
        append_memory_fact(base_dir, args.append_memory)

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

    summary = inspect_workflow_state(base_dir)
    if args.json:
        print(json.dumps(summary, indent=2))
    elif (
        args.show
        or args.init
        or args.append_memory
        or task_loop_text.strip()
        or verification_payload.strip()
    ):
        print(render_text(summary))
    else:
        print(
            "No operation requested. Use --show, --init, --append-memory, "
            "--set-task-loop, or --append-verification-json."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
