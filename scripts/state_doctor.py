#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workflow_state import (
    backup_state_file,
    ensure_state_files,
    get_state_paths,
    inspect_workflow_state,
    load_verification_entries,
    normalize_memory_text,
    normalize_task_loop_text,
    serialize_policy,
)


def build_check_report(base_dir: Path) -> dict[str, Any]:
    summary = inspect_workflow_state(base_dir)
    files = {
        "memory": summary["memory"],
        "policy": {
            "path": summary["policy"]["path"],
            "status": summary["policy"]["status"],
            "errors": summary["policy"]["errors"],
        },
        "task_loop": summary["task_loop"],
        "verification": summary["verification"],
    }

    repairable = [
        name
        for name, status in files.items()
        if status["status"] in {"invalid", "stale"}
        or (name in {"memory", "policy"} and status["status"] == "missing")
    ]
    manual_review_required = [
        name
        for name, status in files.items()
        if status["status"] == "missing" and name in {"task_loop", "verification"}
    ]

    return {
        "workspace_root": summary["workspace_root"],
        "state_dir": summary["state_dir"],
        "files": files,
        "repairable": repairable,
        "manual_review_required": manual_review_required,
    }


def repair_state(base_dir: Path) -> dict[str, Any]:
    ensure_state_files(base_dir)
    paths = get_state_paths(base_dir)
    before = build_check_report(base_dir)
    backups: list[str] = []
    repaired: list[str] = []
    manual_review_required = list(before["manual_review_required"])

    memory_status = before["files"]["memory"]["status"]
    if memory_status in {"missing", "invalid"}:
        backup_path = backup_state_file(base_dir, paths["memory"])
        if backup_path:
            backups.append(backup_path)
        current = paths["memory"].read_text(encoding="utf-8") if paths["memory"].exists() else ""
        paths["memory"].write_text(normalize_memory_text(current), encoding="utf-8")
        repaired.append("memory")

    policy_status = before["files"]["policy"]["status"]
    if policy_status in {"missing", "invalid"}:
        backup_path = backup_state_file(base_dir, paths["policy"])
        if backup_path:
            backups.append(backup_path)
        policy_data = inspect_workflow_state(base_dir)["policy"]["data"]
        paths["policy"].write_text(serialize_policy(policy_data), encoding="utf-8")
        repaired.append("policy")

    task_status = before["files"]["task_loop"]["status"]
    if task_status in {"invalid", "stale"}:
        backup_path = backup_state_file(base_dir, paths["task_loop"])
        if backup_path:
            backups.append(backup_path)
        current = paths["task_loop"].read_text(encoding="utf-8") if paths["task_loop"].exists() else ""
        paths["task_loop"].write_text(normalize_task_loop_text(current), encoding="utf-8")
        repaired.append("task_loop")
        if task_status == "stale":
            manual_review_required.append("task_loop")

    verification_loaded = load_verification_entries(base_dir)
    if verification_loaded["invalid_lines"] > 0:
        backup_path = backup_state_file(base_dir, paths["verification_log"])
        if backup_path:
            backups.append(backup_path)
        serialized = [
            json.dumps(entry, sort_keys=True)
            for entry in verification_loaded["entries"]
        ]
        payload = "\n".join(serialized)
        if payload:
            payload += "\n"
        paths["verification_log"].write_text(payload, encoding="utf-8")
        repaired.append("verification")

    after = build_check_report(base_dir)
    for name, status in after["files"].items():
        if status["status"] in {"missing", "stale"} and name in {"task_loop", "verification"}:
            manual_review_required.append(name)
    return {
        "workspace_root": before["workspace_root"],
        "state_dir": before["state_dir"],
        "repaired": repaired,
        "backups": backups,
        "manual_review_required": sorted(set(manual_review_required)),
        "before": before["files"],
        "after": after["files"],
    }


def render_check(report: dict[str, Any]) -> str:
    lines = [
        f"Workspace root: {report['workspace_root']}",
        f"State dir: {report['state_dir']}",
    ]
    for name, status in report["files"].items():
        lines.append(f"{name}: {status['status']}")
    if report["repairable"]:
        lines.append("Repairable:")
        lines.extend(f"- {name}" for name in report["repairable"])
    if report["manual_review_required"]:
        lines.append("Manual review required:")
        lines.extend(f"- {name}" for name in report["manual_review_required"])
    return "\n".join(lines)


def render_repair(report: dict[str, Any]) -> str:
    lines = [
        f"Repaired files: {', '.join(report['repaired']) if report['repaired'] else 'none'}",
        f"Backups created: {len(report['backups'])}",
    ]
    if report["manual_review_required"]:
        lines.append("Manual review required:")
        lines.extend(f"- {name}" for name in report["manual_review_required"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and repair repo-local workflow state.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument("--check", action="store_true", help="Check workflow state without mutating it.")
    parser.add_argument("--repair", action="store_true", help="Repair malformed workflow state and create backups.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    base_dir = Path(args.repo).expanduser()
    if args.repair:
        result = repair_state(base_dir)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(render_repair(result))
        return 0

    result = build_check_report(base_dir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_check(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
