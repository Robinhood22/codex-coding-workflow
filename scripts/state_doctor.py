#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from team_state import (
    WORKERS_DIRNAME,
    OUTPUTS_DIRNAME,
    append_team_event,
    build_default_manifest,
    get_team_run_paths,
    get_teams_dir,
    validate_team_manifest,
    write_team_manifest,
)
from workflow_state import (
    backup_state_file,
    ensure_state_files,
    get_state_paths,
    inspect_workflow_state,
    load_buglog_entries,
    load_memory_candidate_entries,
    load_memory_sync_entries,
    load_policy,
    load_verification_entries,
    normalize_memory_document_text,
    normalize_memory_text,
    normalize_task_loop_text,
    now_timestamp,
    parse_timestamp,
    serialize_policy,
)


TEAM_ACTIVE_STATUSES = {"running", "synthesizing"}
TEAM_EVENT_TYPES = {
    "run_initialized",
    "worker_added",
    "worker_assignment_written",
    "worker_spawned",
    "worker_status_changed",
    "worker_output_written",
    "run_status_changed",
    "conductor_note",
}


def validate_team_event(event: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not str(event.get("type", "")).strip():
        reasons.append("missing event type")
    elif str(event["type"]) not in TEAM_EVENT_TYPES:
        reasons.append(f"unknown event type {event['type']!r}")
    if parse_timestamp(str(event.get("timestamp", ""))) is None:
        reasons.append("missing or invalid timestamp")
    if not str(event.get("actor", "")).strip():
        reasons.append("missing actor")
    if not isinstance(event.get("details"), dict):
        reasons.append("details must be an object")
    return reasons


def load_team_events_status(events_path: Path) -> dict[str, Any]:
    if not events_path.exists():
        return {
            "path": str(events_path),
            "entries": [],
            "invalid_lines": 0,
            "invalid_reasons": [],
        }

    entries: list[dict[str, Any]] = []
    invalid_lines = 0
    invalid_reasons: list[str] = []
    for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: invalid JSON ({exc}).")
            continue
        if not isinstance(parsed, dict):
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: event must decode to a JSON object.")
            continue
        reasons = validate_team_event(parsed)
        if reasons:
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: {'; '.join(reasons)}.")
            continue
        entries.append(parsed)

    return {
        "path": str(events_path),
        "entries": entries,
        "invalid_lines": invalid_lines,
        "invalid_reasons": invalid_reasons,
    }


def load_raw_team_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {
            "exists": False,
            "status": "missing",
            "data": None,
            "errors": ["manifest.json is missing."],
        }
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "exists": True,
            "status": "invalid",
            "data": None,
            "errors": [f"manifest.json is invalid JSON ({exc})."],
        }
    if not isinstance(raw, dict):
        return {
            "exists": True,
            "status": "invalid",
            "data": None,
            "errors": ["manifest.json must decode to a JSON object."],
        }
    validation_errors = validate_team_manifest(raw)
    return {
        "exists": True,
        "status": "healthy" if not validation_errors else "invalid",
        "data": raw,
        "errors": validation_errors,
    }


def build_worker_recovery_list(paths: dict[str, Path], raw_manifest: dict[str, Any] | None) -> list[str]:
    worker_ids: set[str] = set()
    raw_workers = raw_manifest.get("workers", []) if isinstance(raw_manifest, dict) else []
    for worker in raw_workers:
        if isinstance(worker, dict) and str(worker.get("id", "")).strip():
            worker_ids.add(str(worker["id"]).strip())
    if paths["workers_dir"].exists():
        worker_ids.update(path.stem for path in paths["workers_dir"].glob("*.md"))
    if paths["outputs_dir"].exists():
        worker_ids.update(path.stem for path in paths["outputs_dir"].glob("*.md"))
    return sorted(worker_ids)


def recover_team_manifest(base_dir: Path, run_id: str, raw_manifest: dict[str, Any] | None) -> dict[str, Any]:
    paths = get_team_run_paths(base_dir, run_id)
    workflow = "unknown-workflow"
    owner_skill = "unknown-skill"
    goal = "Recovered team run after malformed or missing manifest."
    mode = "team"
    status = "partial"
    created_at = now_timestamp()
    updated_at = created_at
    conductor_owner = "main-agent"

    if isinstance(raw_manifest, dict):
        workflow = str(raw_manifest.get("workflow") or workflow)
        owner_skill = str(raw_manifest.get("owner_skill") or owner_skill)
        goal = str(raw_manifest.get("goal") or goal)
        mode = str(raw_manifest.get("mode") or mode)
        raw_status = str(raw_manifest.get("status") or "")
        if raw_status:
            status = raw_status
        if parse_timestamp(str(raw_manifest.get("created_at", ""))) is not None:
            created_at = str(raw_manifest["created_at"])
        if parse_timestamp(str(raw_manifest.get("updated_at", ""))) is not None:
            updated_at = str(raw_manifest["updated_at"])
        conductor = raw_manifest.get("conductor", {})
        if isinstance(conductor, dict) and str(conductor.get("owner", "")).strip():
            conductor_owner = str(conductor["owner"]).strip()

    manifest = build_default_manifest(run_id, workflow, goal, owner_skill, mode)
    manifest["status"] = status if status else "partial"
    manifest["created_at"] = created_at
    manifest["updated_at"] = updated_at
    manifest["conductor"] = {"owner": conductor_owner}

    raw_worker_map: dict[str, dict[str, Any]] = {}
    if isinstance(raw_manifest, dict):
        for worker in raw_manifest.get("workers", []):
            if not isinstance(worker, dict):
                continue
            worker_id = str(worker.get("id", "")).strip()
            if worker_id:
                raw_worker_map[worker_id] = worker

    recovered_workers: list[dict[str, Any]] = []
    output_stems = {path.stem for path in paths["outputs_dir"].glob("*.md")}
    assignment_stems = {path.stem for path in paths["workers_dir"].glob("*.md")}

    recovered_ids = build_worker_recovery_list(paths, raw_manifest)
    for worker_id in recovered_ids:
        raw_worker = raw_worker_map.get(worker_id, {})
        recovered_status = str(raw_worker.get("status") or "")
        if worker_id in output_stems and recovered_status in {"", "pending", "running"}:
            recovered_status = "completed"
        if recovered_status not in {"pending", "running", "completed", "failed", "cancelled"}:
            recovered_status = "completed" if worker_id in output_stems else "pending"
        confidence = raw_worker.get("confidence")
        if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
            confidence = None

        worker: dict[str, Any] = {
            "id": worker_id,
            "role": str(raw_worker.get("role") or worker_id),
            "responsibility": raw_worker.get("responsibility"),
            "depends_on": [
                str(dep)
                for dep in raw_worker.get("depends_on", [])
                if isinstance(dep, str) and dep.strip() and str(dep) in recovered_ids
            ],
            "status": recovered_status,
            "agent_id": raw_worker.get("agent_id"),
            "assignment_path": f"{WORKERS_DIRNAME}/{worker_id}.md",
            "output_path": f"{OUTPUTS_DIRNAME}/{worker_id}.md",
            "summary": raw_worker.get("summary"),
            "confidence": confidence,
            "started_at": raw_worker.get("started_at"),
            "completed_at": raw_worker.get("completed_at"),
            "failure_reason": raw_worker.get("failure_reason"),
        }
        if worker_id in assignment_stems and not worker["started_at"] and worker["status"] != "pending":
            worker["started_at"] = created_at
        if worker["status"] in {"completed", "failed", "cancelled"} and not worker["completed_at"]:
            worker["completed_at"] = updated_at
        recovered_workers.append(worker)

    manifest["workers"] = recovered_workers
    if manifest["status"] == "completed" and any(
        worker["status"] != "completed" for worker in recovered_workers
    ):
        manifest["status"] = "partial"
    return manifest


def get_team_stale_after_minutes(base_dir: Path) -> int:
    return int(load_policy(base_dir)["data"]["task_loop"]["stale_after_minutes"])


def inspect_team_run(base_dir: Path, run_id: str, stale_after_minutes: int) -> dict[str, Any]:
    paths = get_team_run_paths(base_dir, run_id)
    manifest_info = load_raw_team_manifest(paths["manifest"])
    events_info = load_team_events_status(paths["events"])

    manifest = manifest_info["data"] if isinstance(manifest_info["data"], dict) else None
    worker_statuses: dict[str, str] = {}
    missing_assignments: list[str] = []
    missing_outputs: list[str] = []

    if isinstance(manifest, dict):
        for worker in manifest.get("workers", []):
            if not isinstance(worker, dict):
                continue
            worker_id = str(worker.get("id", "")).strip()
            if not worker_id:
                continue
            worker_statuses[worker_id] = str(worker.get("status", "unknown"))
            assignment_path = paths["run_dir"] / str(worker.get("assignment_path", ""))
            output_path = paths["run_dir"] / str(worker.get("output_path", ""))
            if not assignment_path.exists():
                missing_assignments.append(worker_id)
            if worker_statuses[worker_id] == "completed" and not output_path.exists():
                missing_outputs.append(worker_id)

    updated_at = str((manifest or {}).get("updated_at") or "")
    updated_dt = parse_timestamp(updated_at)
    stale_active_run = bool(
        manifest
        and str(manifest.get("status")) in TEAM_ACTIVE_STATUSES
        and updated_dt is not None
        and updated_dt < datetime.now().astimezone() - timedelta(minutes=stale_after_minutes)
    )

    repairable: list[str] = []
    manual_review_required: list[str] = []

    if manifest_info["status"] in {"missing", "invalid"}:
        repairable.append("manifest")
    if events_info["invalid_lines"] > 0:
        repairable.append("events")
    if stale_active_run:
        repairable.append("stale-active-run")
    if missing_outputs:
        repairable.append("missing-outputs")
    if missing_assignments:
        manual_review_required.append("missing-assignments")

    overall_status = "healthy"
    if repairable or manual_review_required:
        overall_status = "invalid"
    elif manifest and str(manifest.get("status")) in TEAM_ACTIVE_STATUSES:
        overall_status = "active"

    return {
        "run_id": run_id,
        "path": str(paths["run_dir"]),
        "status": overall_status,
        "run_status": str((manifest or {}).get("status") or "unknown"),
        "updated_at": updated_at or None,
        "worker_count": len(worker_statuses),
        "worker_statuses": worker_statuses,
        "manifest": {
            "path": str(paths["manifest"]),
            "status": manifest_info["status"],
            "errors": manifest_info["errors"],
        },
        "events": {
            "path": str(paths["events"]),
            "invalid_lines": events_info["invalid_lines"],
            "invalid_reasons": events_info["invalid_reasons"],
            "entry_count": len(events_info["entries"]),
        },
        "missing_assignments": missing_assignments,
        "missing_outputs": missing_outputs,
        "stale_active_run": stale_active_run,
        "repairable": repairable,
        "manual_review_required": manual_review_required,
    }


def get_teams_status(base_dir: Path) -> dict[str, Any]:
    teams_dir = get_teams_dir(base_dir)
    stale_after_minutes = get_team_stale_after_minutes(base_dir)
    runs: list[dict[str, Any]] = []
    repairable: list[str] = []
    manual_review_required: list[str] = []

    if teams_dir.exists():
        for child in sorted(teams_dir.iterdir()):
            if not child.is_dir():
                continue
            run_report = inspect_team_run(base_dir, child.name, stale_after_minutes)
            runs.append(run_report)
            if run_report["repairable"]:
                repairable.append(run_report["run_id"])
            if run_report["manual_review_required"]:
                manual_review_required.append(run_report["run_id"])

    if not teams_dir.exists():
        status = "missing"
    elif any(run["status"] == "invalid" for run in runs):
        status = "invalid"
    elif any(run["status"] == "active" for run in runs):
        status = "active"
    else:
        status = "healthy"

    return {
        "path": str(teams_dir),
        "status": status,
        "run_count": len(runs),
        "runs": runs,
        "repairable": repairable,
        "manual_review_required": manual_review_required,
    }


def doctor_note(base_dir: Path, run_id: str, message: str) -> None:
    append_team_event(
        base_dir,
        run_id,
        {
            "type": "conductor_note",
            "actor": "state-doctor",
            "details": {"message": message},
        },
    )


def repair_team_run(base_dir: Path, run_id: str, stale_after_minutes: int) -> dict[str, Any]:
    run_report = inspect_team_run(base_dir, run_id, stale_after_minutes)
    paths = get_team_run_paths(base_dir, run_id)
    backups: list[str] = []
    repaired: list[str] = []
    manual_review_required = list(run_report["manual_review_required"])
    backed_up_targets: set[Path] = set()

    def maybe_backup(target: Path) -> None:
        if target in backed_up_targets:
            return
        backup_path = backup_state_file(base_dir, target)
        if backup_path:
            backups.append(backup_path)
        backed_up_targets.add(target)

    raw_manifest_info = load_raw_team_manifest(paths["manifest"])
    manifest = (
        raw_manifest_info["data"]
        if isinstance(raw_manifest_info["data"], dict)
        else None
    )

    if raw_manifest_info["status"] in {"missing", "invalid"}:
        if paths["manifest"].exists():
            maybe_backup(paths["manifest"])
        manifest = recover_team_manifest(base_dir, run_id, manifest)
        write_team_manifest(base_dir, run_id, manifest)
        repaired.append(f"teams/{run_id}/manifest")
        doctor_note(base_dir, run_id, "Recovered malformed or missing team manifest.")
    elif isinstance(manifest, dict) and raw_manifest_info["errors"]:
        maybe_backup(paths["manifest"])
        manifest = recover_team_manifest(base_dir, run_id, manifest)
        write_team_manifest(base_dir, run_id, manifest)
        repaired.append(f"teams/{run_id}/manifest")
        doctor_note(base_dir, run_id, "Normalized invalid team manifest fields.")

    events_info = load_team_events_status(paths["events"])
    if events_info["invalid_lines"] > 0:
        maybe_backup(paths["events"])
        payload = "\n".join(
            json.dumps(entry, sort_keys=True) for entry in events_info["entries"]
        )
        if payload:
            payload += "\n"
        paths["events"].write_text(payload, encoding="utf-8")
        repaired.append(f"teams/{run_id}/events")

    if manifest is None:
        manifest = recover_team_manifest(base_dir, run_id, None)
        write_team_manifest(base_dir, run_id, manifest)
        repaired.append(f"teams/{run_id}/manifest")

    manifest_changed = False
    now = now_timestamp()
    missing_outputs = set(run_report["missing_outputs"])
    if missing_outputs:
        maybe_backup(paths["manifest"])
        for worker in manifest.get("workers", []):
            if not isinstance(worker, dict):
                continue
            if str(worker.get("id")) not in missing_outputs:
                continue
            worker["status"] = "failed"
            worker["failure_reason"] = "Output artifact missing during state_doctor repair."
            worker["completed_at"] = worker.get("completed_at") or now
            manifest_changed = True
        if manifest.get("status") == "completed":
            manifest["status"] = "partial"
        doctor_note(
            base_dir,
            run_id,
            "Marked workers with missing output artifacts as failed and downgraded run status if needed.",
        )

    if run_report["stale_active_run"]:
        maybe_backup(paths["manifest"])
        for worker in manifest.get("workers", []):
            if not isinstance(worker, dict):
                continue
            if worker.get("status") == "running":
                worker["status"] = "cancelled"
                worker["failure_reason"] = "Stale running worker normalized by state_doctor."
                worker["completed_at"] = worker.get("completed_at") or now
                manifest_changed = True
        if manifest.get("status") in TEAM_ACTIVE_STATUSES:
            manifest["status"] = "partial"
            manifest_changed = True
        doctor_note(
            base_dir,
            run_id,
            "Normalized stale active team run to partial and cancelled stale running workers.",
        )

    if manifest.get("status") == "completed":
        non_completed = [
            worker
            for worker in manifest.get("workers", [])
            if isinstance(worker, dict) and worker.get("status") != "completed"
        ]
        if non_completed:
            maybe_backup(paths["manifest"])
            manifest["status"] = "partial"
            manifest_changed = True
            doctor_note(
                base_dir,
                run_id,
                "Downgraded completed run to partial because not all workers were completed.",
            )

    if manifest_changed:
        write_team_manifest(base_dir, run_id, manifest)
        repaired.append(f"teams/{run_id}/status")

    if run_report["missing_assignments"]:
        manual_review_required.append("missing-assignments")

    return {
        "run_id": run_id,
        "repaired": repaired,
        "backups": backups,
        "manual_review_required": sorted(set(manual_review_required)),
    }


def build_check_report(base_dir: Path) -> dict[str, Any]:
    summary = inspect_workflow_state(base_dir)
    teams = get_teams_status(base_dir)
    files = {
        "memory": summary["memory"],
        "shared_memory": summary["shared_memory"],
        "memory_candidates": summary["memory_candidates"],
        "memory_sync": summary["memory_sync"],
        "buglog": summary["buglog"],
        "policy": {
            "path": summary["policy"]["path"],
            "status": summary["policy"]["status"],
            "errors": summary["policy"]["errors"],
        },
        "task_loop": summary["task_loop"],
        "verification": summary["verification"],
        "teams": {
            "path": teams["path"],
            "status": teams["status"],
            "run_count": teams["run_count"],
        },
    }

    repairable = [
        name
        for name, status in files.items()
        if status["status"] in {"invalid", "stale"}
        or (name in {"memory", "shared_memory", "policy"} and status["status"] == "missing")
    ]
    repairable.extend(f"teams/{run_id}" for run_id in teams["repairable"])

    manual_review_required = [
        name
        for name, status in files.items()
        if status["status"] == "missing" and name in {"task_loop", "verification"}
    ]
    manual_review_required.extend(f"teams/{run_id}" for run_id in teams["manual_review_required"])

    return {
        "workspace_root": summary["workspace_root"],
        "state_dir": summary["state_dir"],
        "files": files,
        "teams": teams,
        "repairable": sorted(set(repairable)),
        "manual_review_required": sorted(set(manual_review_required)),
    }


def repair_state(base_dir: Path) -> dict[str, Any]:
    ensure_state_files(base_dir)
    paths = get_state_paths(base_dir)
    before = build_check_report(base_dir)
    backups: list[str] = []
    repaired: list[str] = []
    data_loss: list[str] = []
    manual_review_required = list(before["manual_review_required"])

    memory_status = before["files"]["memory"]["status"]
    if memory_status in {"missing", "invalid"}:
        backup_path = backup_state_file(base_dir, paths["memory"])
        if backup_path:
            backups.append(backup_path)
        current = paths["memory"].read_text(encoding="utf-8") if paths["memory"].exists() else ""
        paths["memory"].write_text(normalize_memory_text(current), encoding="utf-8")
        repaired.append("memory")

    shared_memory_status = before["files"]["shared_memory"]["status"]
    if shared_memory_status in {"missing", "invalid"}:
        backup_path = backup_state_file(base_dir, paths["shared_memory"])
        if backup_path:
            backups.append(backup_path)
        current = (
            paths["shared_memory"].read_text(encoding="utf-8")
            if paths["shared_memory"].exists()
            else ""
        )
        paths["shared_memory"].write_text(
            normalize_memory_document_text(current, "# Shared Memory"),
            encoding="utf-8",
        )
        repaired.append("shared_memory")

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

    buglog_loaded = load_buglog_entries(base_dir)
    if buglog_loaded["invalid_lines"] > 0:
        backup_path = backup_state_file(base_dir, paths["buglog"])
        if backup_path:
            backups.append(backup_path)
        serialized = [
            json.dumps(entry, sort_keys=True)
            for entry in buglog_loaded["entries"]
        ]
        payload = "\n".join(serialized)
        if payload:
            payload += "\n"
        paths["buglog"].write_text(payload, encoding="utf-8")
        repaired.append("buglog")
        data_loss.append(
            "buglog: dropped "
            f"{buglog_loaded['invalid_lines']} invalid line(s) while preserving "
            f"{len(buglog_loaded['entries'])} valid entr"
            f"{'y' if len(buglog_loaded['entries']) == 1 else 'ies'}."
        )

    memory_candidates_loaded = load_memory_candidate_entries(base_dir)
    if memory_candidates_loaded["invalid_lines"] > 0:
        backup_path = backup_state_file(base_dir, paths["memory_candidates"])
        if backup_path:
            backups.append(backup_path)
        serialized = [
            json.dumps(entry, sort_keys=True)
            for entry in memory_candidates_loaded["entries"]
        ]
        payload = "\n".join(serialized)
        if payload:
            payload += "\n"
        paths["memory_candidates"].write_text(payload, encoding="utf-8")
        repaired.append("memory_candidates")

    memory_sync_loaded = load_memory_sync_entries(base_dir)
    if memory_sync_loaded["invalid_lines"] > 0:
        backup_path = backup_state_file(base_dir, paths["memory_sync_log"])
        if backup_path:
            backups.append(backup_path)
        serialized = [
            json.dumps(entry, sort_keys=True)
            for entry in memory_sync_loaded["entries"]
        ]
        payload = "\n".join(serialized)
        if payload:
            payload += "\n"
        paths["memory_sync_log"].write_text(payload, encoding="utf-8")
        repaired.append("memory_sync")

    stale_after_minutes = get_team_stale_after_minutes(base_dir)
    for run in before["teams"]["runs"]:
        if not run["repairable"]:
            continue
        result = repair_team_run(base_dir, run["run_id"], stale_after_minutes)
        repaired.extend(result["repaired"])
        backups.extend(result["backups"])
        if result["manual_review_required"]:
            manual_review_required.append(f"teams/{run['run_id']}")

    after = build_check_report(base_dir)
    for name, status in after["files"].items():
        if status["status"] in {"missing", "stale"} and name in {"task_loop", "verification"}:
            manual_review_required.append(name)
    manual_review_required.extend(
        f"teams/{run_id}" for run_id in after["teams"]["manual_review_required"]
    )

    return {
        "workspace_root": before["workspace_root"],
        "state_dir": before["state_dir"],
        "repaired": sorted(set(repaired)),
        "backups": backups,
        "data_loss": data_loss,
        "manual_review_required": sorted(set(manual_review_required)),
        "before": before["files"],
        "after": after["files"],
        "teams_before": before["teams"],
        "teams_after": after["teams"],
    }


def render_check(report: dict[str, Any]) -> str:
    lines = [
        f"Workspace root: {report['workspace_root']}",
        f"State dir: {report['state_dir']}",
    ]
    for name, status in report["files"].items():
        extra = ""
        if name == "teams":
            extra = f" ({status.get('run_count', 0)} runs)"
        lines.append(f"{name}: {status['status']}{extra}")
    if report["teams"]["runs"]:
        lines.append("Team runs:")
        for run in report["teams"]["runs"]:
            details: list[str] = [run["run_status"]]
            if run["repairable"]:
                details.append("repairable")
            if run["manual_review_required"]:
                details.append("manual-review")
            lines.append(f"- {run['run_id']}: {', '.join(details)}")
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
    if report.get("data_loss"):
        lines.append("Data loss:")
        lines.extend(f"- {item}" for item in report["data_loss"])
    teams_after = report.get("teams_after", {})
    if teams_after.get("runs"):
        lines.append("Team runs after repair:")
        for run in teams_after["runs"]:
            lines.append(f"- {run['run_id']}: {run['run_status']}")
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
