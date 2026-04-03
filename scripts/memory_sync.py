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
    ensure_task_stream,
    ensure_state_files,
    inspect_workflow_state,
    list_task_streams,
    normalize_stream_id,
    set_primary_task_stream,
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
    task_loop = summary["task_loop"]
    lines = [
        f"Workspace root: {summary['workspace_root']}",
        f"State dir: {summary['state_dir']}",
        f"Memory: {summary['memory']['status']}",
        f"Policy: {summary['policy']['status']}",
        f"Task loop: {task_loop['status']} ({task_loop.get('mode', 'legacy')})",
        f"Verification: {summary['verification']['status']}",
    ]
    if task_loop.get("mode") == "streams":
        lines.append(
            "Streams: "
            f"{task_loop.get('stream_count', 0)} total, "
            f"{task_loop.get('open_stream_count', 0)} open, "
            f"primary={task_loop.get('primary_stream_id') or 'none'}"
        )

    errors = summary["policy"]["errors"]
    if errors:
        lines.append("Policy notes:")
        lines.extend(f"- {error}" for error in errors)

    return "\n".join(lines)


def render_streams(streams: list[dict[str, Any]]) -> str:
    if not streams:
        return "Streams:\n- none"

    lines = ["Streams:"]
    for stream in streams:
        primary = " primary" if stream.get("is_primary") else ""
        lines.append(
            f"- {stream['id']}: {stream['status']} ({stream['state']}{primary})"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage repo-local workflow state.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument("--init", action="store_true", help="Create missing workflow state files.")
    parser.add_argument("--show", action="store_true", help="Show the current workflow state summary.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--list-streams", action="store_true", help="List task streams in the current workflow state.")
    parser.add_argument("--stream", type=str, default=None, help="Target a named task stream for task-loop or verification updates.")
    parser.add_argument("--stream-title", type=str, default=None, help="Set or update the task stream title.")
    parser.add_argument(
        "--stream-state",
        type=str,
        default=None,
        choices=["open", "paused", "closed"],
        help="Set or update the task stream state.",
    )
    parser.add_argument(
        "--set-primary-stream",
        type=str,
        default=None,
        help="Mark the named task stream as the primary stream.",
    )
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

    if args.stream and (args.stream_title or args.stream_state) and not (
        args.set_task_loop or args.task_loop_file
    ):
        ensure_task_stream(
            base_dir,
            args.stream,
            title=args.stream_title,
            state=args.stream_state,
        )

    if args.set_primary_stream:
        set_primary_task_stream(base_dir, args.set_primary_stream)

    task_loop_text = (
        load_text_argument(args.set_task_loop, args.task_loop_file)
        if args.set_task_loop or args.task_loop_file
        else ""
    )
    if task_loop_text.strip():
        target_stream = args.stream
        make_primary = False
        if target_stream:
            normalized_stream = normalize_stream_id(target_stream)
            requested_primary = (
                normalize_stream_id(args.set_primary_stream)
                if args.set_primary_stream
                else None
            )
            make_primary = requested_primary == normalized_stream
        update_task_loop(
            base_dir,
            task_loop_text,
            stream_id=target_stream,
            stream_title=args.stream_title,
            stream_state=args.stream_state,
            set_primary=make_primary,
        )

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
        if args.stream and "stream_id" not in parsed:
            parsed["stream_id"] = normalize_stream_id(args.stream)
        append_verification_entry(base_dir, parsed)

    summary = inspect_workflow_state(base_dir)
    streams = list_task_streams(base_dir)
    if args.json:
        print(json.dumps(summary, indent=2))
    elif (
        args.show
        or args.init
        or args.list_streams
        or args.stream
        or args.stream_title
        or args.stream_state
        or args.set_primary_stream
        or args.append_memory
        or task_loop_text.strip()
        or verification_payload.strip()
    ):
        output = render_text(summary)
        if args.list_streams or summary["task_loop"].get("mode") == "streams":
            output += "\n" + render_streams(streams)
        print(output)
    else:
        print(
            "No operation requested. Use --show, --init, --append-memory, "
            "--set-task-loop, --stream, --list-streams, or --append-verification-json."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
