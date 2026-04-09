#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workflow_state import (
    find_workspace_root,
    get_task_loop_status,
    get_verification_state,
    load_verification_entries,
)


def build_verification_summary(base_dir: Path) -> dict[str, Any]:
    workspace_root = find_workspace_root(base_dir)
    task_loop = get_task_loop_status(base_dir)
    verification = get_verification_state(base_dir, task_loop)
    loaded = load_verification_entries(base_dir)
    latest_entry = loaded["entries"][-1] if loaded["entries"] else None
    checks = latest_entry.get("checks", []) if latest_entry else []

    failing_checks = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        result = str(check.get("result", "")).upper()
        if result == "FAIL":
            failing_checks.append(
                {
                    "name": check.get("name", "unnamed check"),
                    "command": check.get("command"),
                    "summary": check.get("summary"),
                }
            )

    blockers: list[str] = []
    if verification["status"] == "missing":
        blockers.append("No verification evidence is logged.")
    if verification["status"] == "stale":
        blockers.append("Latest verification evidence predates the current task loop.")
    if verification["status"] == "invalid":
        blockers.append("Verification log contains malformed entries.")
    if latest_entry and str(latest_entry.get("verdict", "")).upper() == "FAIL":
        blockers.append("Latest verification verdict is FAIL.")

    stream_coverage = verification.get("stream_coverage", [])
    for stream_id in verification.get("uncovered_open_streams", []):
        blockers.append(f"Task stream {stream_id} is missing current verification coverage.")

    return {
        "workspace_root": str(workspace_root),
        "task_loop_status": task_loop["status"],
        "task_loop_mode": task_loop.get("mode", "legacy"),
        "status": verification["status"],
        "entry_count": verification["entry_count"],
        "invalid_lines": verification["invalid_lines"],
        "latest_timestamp": verification["latest_timestamp"],
        "latest_verdict": verification["latest_verdict"],
        "latest_checks_count": len(checks),
        "failing_checks": failing_checks,
        "stream_coverage": stream_coverage,
        "covers_current_task_loop": verification["status"] == "present",
        "blockers": blockers,
    }


def render_text(summary: dict[str, Any]) -> str:
    lines = [
        f"Verification state: {summary['status']}",
        f"Latest verdict: {summary['latest_verdict'] or 'none'}",
        f"Latest timestamp: {summary['latest_timestamp'] or 'none'}",
        f"Valid entries: {summary['entry_count']}",
        f"Invalid lines: {summary['invalid_lines']}",
        f"Task-loop coverage: {'yes' if summary['covers_current_task_loop'] else 'no'}",
    ]
    if summary["failing_checks"]:
        lines.append("Failing checks:")
        lines.extend(
            f"- {check['name']}: {check.get('summary') or 'no summary'}"
            for check in summary["failing_checks"]
        )
    if summary.get("stream_coverage"):
        lines.append("Stream coverage:")
        lines.extend(
            f"- {item['id']}: {'covered' if item['covered'] else 'missing'}"
            for item in summary["stream_coverage"]
        )
    if summary["blockers"]:
        lines.append("Blockers:")
        lines.extend(f"- {blocker}" for blocker in summary["blockers"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize workflow verification evidence.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    summary = build_verification_summary(Path(args.repo).expanduser())
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
