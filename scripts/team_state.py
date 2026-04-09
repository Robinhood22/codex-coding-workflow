#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from workflow_state import ensure_state_files, find_workspace_root

TEAM_SCHEMA_VERSION = 1
VALID_RUN_STATUSES = {
    "planned",
    "running",
    "synthesizing",
    "completed",
    "partial",
    "failed",
    "cancelled",
}
VALID_WORKER_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
}
WORKERS_DIRNAME = "workers"
OUTPUTS_DIRNAME = "outputs"


def now_timestamp() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_text_argument(inline_text: str | None, file_path: str | None) -> str:
    if inline_text:
        return inline_text
    if file_path:
        return Path(file_path).expanduser().read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered)
    return collapsed.strip("-") or "run"


def generate_run_id(workflow: str) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"{slugify(workflow)}-{stamp}"


def get_teams_dir(base_dir: Path) -> Path:
    return find_workspace_root(base_dir) / ".codex-workflows" / "teams"


def get_team_run_dir(base_dir: Path, run_id: str) -> Path:
    return get_teams_dir(base_dir) / run_id


def get_team_run_paths(base_dir: Path, run_id: str) -> dict[str, Path]:
    run_dir = get_team_run_dir(base_dir, run_id)
    return {
        "run_dir": run_dir,
        "manifest": run_dir / "manifest.json",
        "execution": run_dir / "execution.json",
        "plan": run_dir / "plan.md",
        "events": run_dir / "events.jsonl",
        "workers_dir": run_dir / WORKERS_DIRNAME,
        "outputs_dir": run_dir / OUTPUTS_DIRNAME,
        "summary": run_dir / "summary.md",
    }


def ensure_team_run_dirs(base_dir: Path, run_id: str) -> dict[str, Path]:
    teams_dir = get_teams_dir(base_dir)
    teams_dir.mkdir(parents=True, exist_ok=True)
    paths = get_team_run_paths(base_dir, run_id)
    paths["run_dir"].mkdir(parents=True, exist_ok=True)
    paths["workers_dir"].mkdir(parents=True, exist_ok=True)
    paths["outputs_dir"].mkdir(parents=True, exist_ok=True)
    return paths


def normalize_worker_id(worker_id: str) -> str:
    return slugify(worker_id)


def normalize_list_argument(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [part.strip() for part in raw.split(",")]
    return [part for part in parts if part]


def build_default_manifest(
    run_id: str,
    workflow: str,
    goal: str,
    owner_skill: str,
    mode: str,
) -> dict[str, Any]:
    timestamp = now_timestamp()
    return {
        "schema_version": TEAM_SCHEMA_VERSION,
        "run_id": run_id,
        "workflow": workflow,
        "owner_skill": owner_skill,
        "mode": mode,
        "goal": goal,
        "status": "planned",
        "created_at": timestamp,
        "updated_at": timestamp,
        "conductor": {
            "owner": "main-agent",
        },
        "workers": [],
    }


def validate_team_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if manifest.get("schema_version") != TEAM_SCHEMA_VERSION:
        errors.append(
            f"schema_version is {manifest.get('schema_version')!r}; expected {TEAM_SCHEMA_VERSION}."
        )
    if not str(manifest.get("run_id", "")).strip():
        errors.append("run_id is required.")
    if manifest.get("status") not in VALID_RUN_STATUSES:
        errors.append(f"run status {manifest.get('status')!r} is invalid.")

    workers = manifest.get("workers")
    if not isinstance(workers, list):
        errors.append("workers must be a list.")
        return errors

    worker_ids: set[str] = set()
    for index, worker in enumerate(workers):
        if not isinstance(worker, dict):
            errors.append(f"worker[{index}] must be an object.")
            continue

        worker_id = str(worker.get("id", "")).strip()
        if not worker_id:
            errors.append(f"worker[{index}] is missing id.")
            continue
        if worker_id in worker_ids:
            errors.append(f"worker id {worker_id!r} is duplicated.")
        worker_ids.add(worker_id)

        if worker.get("status") not in VALID_WORKER_STATUSES:
            errors.append(
                f"worker {worker_id!r} has invalid status {worker.get('status')!r}."
            )

        depends_on = worker.get("depends_on", [])
        if not isinstance(depends_on, list):
            errors.append(f"worker {worker_id!r} depends_on must be a list.")

        assignment_path = str(worker.get("assignment_path", ""))
        if not assignment_path.startswith(f"{WORKERS_DIRNAME}/"):
            errors.append(
                f"worker {worker_id!r} assignment_path must live under {WORKERS_DIRNAME}/."
            )

        output_path = str(worker.get("output_path", ""))
        if not output_path.startswith(f"{OUTPUTS_DIRNAME}/"):
            errors.append(
                f"worker {worker_id!r} output_path must live under {OUTPUTS_DIRNAME}/."
            )

        confidence = worker.get("confidence")
        if confidence is not None and not (
            isinstance(confidence, (int, float)) and 0.0 <= float(confidence) <= 1.0
        ):
            errors.append(
                f"worker {worker_id!r} confidence must be between 0.0 and 1.0."
            )

    for worker in workers:
        if not isinstance(worker, dict):
            continue
        worker_id = str(worker.get("id", "")).strip()
        for dependency in worker.get("depends_on", []):
            if dependency not in worker_ids:
                errors.append(
                    f"worker {worker_id!r} depends on unknown worker {dependency!r}."
                )

    return errors


def load_team_manifest(base_dir: Path, run_id: str) -> dict[str, Any]:
    path = get_team_run_paths(base_dir, run_id)["manifest"]
    if not path.exists():
        raise SystemExit(f"Run {run_id!r} does not exist.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"manifest.json is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SystemExit("manifest.json must decode to a JSON object.")
    return manifest


def write_team_manifest(base_dir: Path, run_id: str, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = now_timestamp()
    errors = validate_team_manifest(manifest)
    if errors:
        raise SystemExit("Invalid manifest:\n- " + "\n- ".join(errors))
    path = ensure_team_run_dirs(base_dir, run_id)["manifest"]
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def append_team_event(base_dir: Path, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise SystemExit("Event payload must be a JSON object.")

    event_payload = dict(event)
    event_payload.setdefault("timestamp", now_timestamp())
    event_payload.setdefault("actor", "conductor")
    event_payload.setdefault("details", {})

    if "type" not in event_payload or not str(event_payload["type"]).strip():
        raise SystemExit("Event payload requires a non-empty type.")
    if not isinstance(event_payload["details"], dict):
        raise SystemExit("Event details must be a JSON object.")

    events_path = ensure_team_run_dirs(base_dir, run_id)["events"]
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event_payload, sort_keys=True) + "\n")

    return event_payload


def init_run(
    base_dir: Path,
    workflow: str,
    goal: str,
    owner_skill: str,
    run_id: str | None = None,
    mode: str = "team",
) -> dict[str, Any]:
    resolved_run_id = run_id or generate_run_id(workflow)
    paths = get_team_run_paths(base_dir, resolved_run_id)
    if paths["manifest"].exists():
        raise SystemExit(f"Run {resolved_run_id!r} already exists.")

    ensure_team_run_dirs(base_dir, resolved_run_id)
    manifest = build_default_manifest(
        run_id=resolved_run_id,
        workflow=workflow,
        goal=goal,
        owner_skill=owner_skill,
        mode=mode,
    )
    write_team_manifest(base_dir, resolved_run_id, manifest)
    paths["plan"].write_text(
        f"# Team Run Plan\n\nGoal: {goal}\n\nWorkflow: {workflow}\n",
        encoding="utf-8",
    )
    append_team_event(
        base_dir,
        resolved_run_id,
        {
            "type": "run_initialized",
            "actor": "conductor",
            "details": {
                "workflow": workflow,
                "owner_skill": owner_skill,
                "mode": mode,
            },
        },
    )
    return build_run_summary(base_dir, resolved_run_id)


def add_worker(
    base_dir: Path,
    run_id: str,
    worker_id: str,
    role: str,
    responsibility: str | None = None,
    depends_on: list[str] | None = None,
    assignment_text: str | None = None,
) -> dict[str, Any]:
    normalized_worker_id = normalize_worker_id(worker_id)
    manifest = load_team_manifest(base_dir, run_id)
    workers = manifest.setdefault("workers", [])
    if any(worker.get("id") == normalized_worker_id for worker in workers):
        raise SystemExit(f"Worker {normalized_worker_id!r} already exists.")

    dependencies = depends_on or []
    known_workers = {str(worker.get("id")) for worker in workers if isinstance(worker, dict)}
    unknown = [dep for dep in dependencies if dep not in known_workers]
    if unknown:
        raise SystemExit("Unknown worker dependency: " + ", ".join(unknown))

    assignment_path = f"{WORKERS_DIRNAME}/{normalized_worker_id}.md"
    output_path = f"{OUTPUTS_DIRNAME}/{normalized_worker_id}.md"
    worker = {
        "id": normalized_worker_id,
        "role": role,
        "responsibility": responsibility,
        "depends_on": dependencies,
        "status": "pending",
        "agent_id": None,
        "assignment_path": assignment_path,
        "output_path": output_path,
        "summary": None,
        "confidence": None,
        "started_at": None,
        "completed_at": None,
        "failure_reason": None,
    }
    workers.append(worker)
    write_team_manifest(base_dir, run_id, manifest)

    if assignment_text:
        write_worker_assignment(base_dir, run_id, normalized_worker_id, assignment_text)

    append_team_event(
        base_dir,
        run_id,
        {
            "type": "worker_added",
            "actor": "conductor",
            "target": normalized_worker_id,
            "details": {
                "role": role,
                "responsibility": responsibility,
                "depends_on": dependencies,
            },
        },
    )
    return build_run_summary(base_dir, run_id)


def write_worker_assignment(base_dir: Path, run_id: str, worker_id: str, text: str) -> dict[str, Any]:
    normalized_worker_id = normalize_worker_id(worker_id)
    manifest = load_team_manifest(base_dir, run_id)
    worker = next(
        (item for item in manifest["workers"] if item.get("id") == normalized_worker_id),
        None,
    )
    if worker is None:
        raise SystemExit(f"Worker {normalized_worker_id!r} does not exist.")

    paths = ensure_team_run_dirs(base_dir, run_id)
    assignment_path = paths["workers_dir"] / f"{normalized_worker_id}.md"
    assignment_path.write_text(text.rstrip() + "\n", encoding="utf-8")

    append_team_event(
        base_dir,
        run_id,
        {
            "type": "worker_assignment_written",
            "actor": "conductor",
            "target": normalized_worker_id,
            "details": {
                "assignment_path": str(Path(worker["assignment_path"])),
            },
        },
    )
    return build_run_summary(base_dir, run_id)


def set_worker_status(
    base_dir: Path,
    run_id: str,
    worker_id: str,
    status: str,
    reason: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    if status not in VALID_WORKER_STATUSES:
        raise SystemExit(f"Invalid worker status: {status!r}")

    normalized_worker_id = normalize_worker_id(worker_id)
    manifest = load_team_manifest(base_dir, run_id)
    worker = next(
        (item for item in manifest["workers"] if item.get("id") == normalized_worker_id),
        None,
    )
    if worker is None:
        raise SystemExit(f"Worker {normalized_worker_id!r} does not exist.")

    previous_status = worker["status"]
    worker["status"] = status
    if status == "running" and not worker.get("started_at"):
        worker["started_at"] = now_timestamp()
    if status in {"completed", "failed", "cancelled"}:
        worker["completed_at"] = now_timestamp()
    if status == "failed":
        worker["failure_reason"] = reason
    elif reason and status == "cancelled":
        worker["failure_reason"] = reason
    else:
        worker["failure_reason"] = None
    if agent_id is not None:
        worker["agent_id"] = agent_id

    write_team_manifest(base_dir, run_id, manifest)
    append_team_event(
        base_dir,
        run_id,
        {
            "type": "worker_status_changed",
            "actor": "conductor",
            "target": normalized_worker_id,
            "details": {
                "from": previous_status,
                "to": status,
                "reason": reason,
                "agent_id": agent_id,
            },
        },
    )
    return build_run_summary(base_dir, run_id)


def write_worker_output(
    base_dir: Path,
    run_id: str,
    worker_id: str,
    text: str,
    summary: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    normalized_worker_id = normalize_worker_id(worker_id)
    manifest = load_team_manifest(base_dir, run_id)
    worker = next(
        (item for item in manifest["workers"] if item.get("id") == normalized_worker_id),
        None,
    )
    if worker is None:
        raise SystemExit(f"Worker {normalized_worker_id!r} does not exist.")
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        raise SystemExit("confidence must be between 0.0 and 1.0.")

    paths = ensure_team_run_dirs(base_dir, run_id)
    output_path = paths["outputs_dir"] / f"{normalized_worker_id}.md"
    output_path.write_text(text.rstrip() + "\n", encoding="utf-8")

    worker["summary"] = summary
    worker["confidence"] = confidence
    if worker["status"] in {"pending", "running"}:
        worker["status"] = "completed"
        worker["completed_at"] = now_timestamp()
        if not worker.get("started_at"):
            worker["started_at"] = worker["completed_at"]

    write_team_manifest(base_dir, run_id, manifest)
    append_team_event(
        base_dir,
        run_id,
        {
            "type": "worker_output_written",
            "actor": "conductor",
            "target": normalized_worker_id,
            "details": {
                "summary": summary,
                "confidence": confidence,
            },
        },
    )
    return build_run_summary(base_dir, run_id)


def set_run_status(
    base_dir: Path,
    run_id: str,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    if status not in VALID_RUN_STATUSES:
        raise SystemExit(f"Invalid run status: {status!r}")

    manifest = load_team_manifest(base_dir, run_id)
    previous_status = manifest["status"]
    manifest["status"] = status
    write_team_manifest(base_dir, run_id, manifest)
    append_team_event(
        base_dir,
        run_id,
        {
            "type": "run_status_changed",
            "actor": "conductor",
            "details": {
                "from": previous_status,
                "to": status,
                "reason": reason,
            },
        },
    )
    return build_run_summary(base_dir, run_id)


def load_team_events(base_dir: Path, run_id: str) -> list[dict[str, Any]]:
    path = get_team_run_paths(base_dir, run_id)["events"]
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def load_execution_summary(base_dir: Path, run_id: str) -> dict[str, Any]:
    path = get_team_run_paths(base_dir, run_id)["execution"]
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "status": "missing",
            "execution_mode": None,
            "execution_status": None,
            "worktree_path": None,
            "worktree_branch": None,
            "repo_root": None,
            "errors": [],
        }

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "path": str(path),
            "exists": True,
            "status": "invalid",
            "execution_mode": None,
            "execution_status": None,
            "worktree_path": None,
            "worktree_branch": None,
            "repo_root": None,
            "errors": [f"execution.json is invalid JSON ({exc})."],
        }

    if not isinstance(parsed, dict):
        return {
            "path": str(path),
            "exists": True,
            "status": "invalid",
            "execution_mode": None,
            "execution_status": None,
            "worktree_path": None,
            "worktree_branch": None,
            "repo_root": None,
            "errors": ["execution.json must decode to a JSON object."],
        }

    execution_mode = parsed.get("execution_mode")
    execution_status = parsed.get("status")
    errors: list[str] = []
    if execution_mode is not None and not str(execution_mode).strip():
        errors.append("execution_mode must be non-empty when present.")
    if execution_status is not None and not str(execution_status).strip():
        errors.append("execution status must be non-empty when present.")

    return {
        "path": str(path),
        "exists": True,
        "status": "invalid" if errors else "present",
        "execution_mode": execution_mode,
        "execution_status": execution_status,
        "worktree_path": parsed.get("worktree_path"),
        "worktree_branch": parsed.get("worktree_branch"),
        "repo_root": parsed.get("repo_root"),
        "base_branch": parsed.get("base_branch"),
        "base_commit": parsed.get("base_commit"),
        "cleanup_policy": parsed.get("cleanup_policy"),
        "cleanup_status": parsed.get("cleanup_status"),
        "dirty_repo_policy": parsed.get("dirty_repo_policy"),
        "last_verified_head": parsed.get("last_verified_head"),
        "raw": parsed,
        "errors": errors,
    }


def get_worker_entry(manifest: dict[str, Any], worker_id: str) -> dict[str, Any]:
    normalized_worker_id = normalize_worker_id(worker_id)
    worker = next(
        (
            item
            for item in manifest.get("workers", [])
            if isinstance(item, dict) and item.get("id") == normalized_worker_id
        ),
        None,
    )
    if worker is None:
        raise SystemExit(f"Worker {normalized_worker_id!r} does not exist.")
    return worker


def build_run_summary(base_dir: Path, run_id: str) -> dict[str, Any]:
    manifest = load_team_manifest(base_dir, run_id)
    paths = get_team_run_paths(base_dir, run_id)
    events = load_team_events(base_dir, run_id)
    validation_errors = validate_team_manifest(manifest)
    execution = load_execution_summary(base_dir, run_id)

    worker_statuses: dict[str, str] = {}
    worker_details: dict[str, dict[str, Any]] = {}
    missing_assignments: list[str] = []
    missing_outputs: list[str] = []
    agent_assigned_workers: list[str] = []
    for worker in manifest.get("workers", []):
        if not isinstance(worker, dict):
            continue
        worker_id = str(worker.get("id", ""))
        worker_statuses[worker_id] = str(worker.get("status", "unknown"))
        assignment_path = paths["run_dir"] / str(worker.get("assignment_path", ""))
        output_path = paths["run_dir"] / str(worker.get("output_path", ""))
        if not assignment_path.exists():
            missing_assignments.append(worker_id)
        worker_details[worker_id] = {
            "id": worker_id,
            "role": worker.get("role"),
            "responsibility": worker.get("responsibility"),
            "depends_on": worker.get("depends_on", []),
            "status": worker.get("status"),
            "agent_id": worker.get("agent_id"),
            "assignment_path": str(worker.get("assignment_path", "")),
            "assignment_abspath": str(assignment_path),
            "output_path": str(worker.get("output_path", "")),
            "output_abspath": str(output_path),
            "summary": worker.get("summary"),
            "confidence": worker.get("confidence"),
            "started_at": worker.get("started_at"),
            "completed_at": worker.get("completed_at"),
            "failure_reason": worker.get("failure_reason"),
        }
        if worker.get("agent_id"):
            agent_assigned_workers.append(worker_id)
        if worker_statuses[worker_id] == "completed" and not output_path.exists():
            missing_outputs.append(worker_id)

    counts = {status: 0 for status in VALID_WORKER_STATUSES}
    for status in worker_statuses.values():
        if status in counts:
            counts[status] += 1

    return {
        "run_id": run_id,
        "workspace_root": str(find_workspace_root(base_dir)),
        "run_dir": str(paths["run_dir"]),
        "workflow": manifest.get("workflow"),
        "owner_skill": manifest.get("owner_skill"),
        "goal": manifest.get("goal"),
        "mode": manifest.get("mode"),
        "status": manifest.get("status"),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "worker_count": len(worker_statuses),
        "worker_statuses": worker_statuses,
        "worker_counts": counts,
        "worker_details": worker_details,
        "events_count": len(events),
        "agent_assigned_workers": agent_assigned_workers,
        "missing_assignments": missing_assignments,
        "missing_outputs": missing_outputs,
        "validation_errors": validation_errors,
        "execution": execution,
    }


def build_worker_summary(
    base_dir: Path,
    run_id: str,
    worker_id: str,
    include_assignment: bool = False,
    include_output: bool = False,
) -> dict[str, Any]:
    manifest = load_team_manifest(base_dir, run_id)
    worker = get_worker_entry(manifest, worker_id)
    paths = get_team_run_paths(base_dir, run_id)
    execution = load_execution_summary(base_dir, run_id)
    assignment_path = paths["run_dir"] / str(worker.get("assignment_path", ""))
    output_path = paths["run_dir"] / str(worker.get("output_path", ""))

    result: dict[str, Any] = {
        "run_id": run_id,
        "workspace_root": str(find_workspace_root(base_dir)),
        "workflow": manifest.get("workflow"),
        "owner_skill": manifest.get("owner_skill"),
        "goal": manifest.get("goal"),
        "run_status": manifest.get("status"),
        "execution": execution,
        "worker": {
            "id": worker.get("id"),
            "role": worker.get("role"),
            "responsibility": worker.get("responsibility"),
            "depends_on": worker.get("depends_on", []),
            "status": worker.get("status"),
            "agent_id": worker.get("agent_id"),
            "assignment_path": str(worker.get("assignment_path", "")),
            "assignment_abspath": str(assignment_path),
            "output_path": str(worker.get("output_path", "")),
            "output_abspath": str(output_path),
            "summary": worker.get("summary"),
            "confidence": worker.get("confidence"),
            "started_at": worker.get("started_at"),
            "completed_at": worker.get("completed_at"),
            "failure_reason": worker.get("failure_reason"),
        },
    }

    if include_assignment:
        result["assignment_text"] = (
            assignment_path.read_text(encoding="utf-8") if assignment_path.exists() else ""
        )
    if include_output:
        result["output_text"] = (
            output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        )
    return result


def list_runs(base_dir: Path) -> list[dict[str, Any]]:
    teams_dir = get_teams_dir(base_dir)
    if not teams_dir.exists():
        return []

    runs: list[dict[str, Any]] = []
    for child in sorted(teams_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            summary = build_run_summary(base_dir, child.name)
        except SystemExit:
            continue
        runs.append(summary)
    return runs


def render_text(summary: dict[str, Any]) -> str:
    if "runs" in summary:
        runs = summary["runs"]
        if not runs:
            return "No team runs found."
        lines = ["Team runs:"]
        for run in runs:
            lines.append(
                f"- {run['run_id']}: {run['status']} ({run.get('workflow')})"
            )
        return "\n".join(lines)

    if "worker" in summary:
        worker = summary["worker"]
        lines = [
            f"Run: {summary['run_id']}",
            f"Workflow: {summary.get('workflow') or 'unknown'}",
            f"Run status: {summary.get('run_status') or 'unknown'}",
            "Execution: "
            f"{summary.get('execution', {}).get('execution_mode') or 'none'} "
            f"({summary.get('execution', {}).get('execution_status') or summary.get('execution', {}).get('status') or 'unknown'})",
            f"Worker: {worker.get('id') or 'unknown'}",
            f"Role: {worker.get('role') or 'unknown'}",
            f"Status: {worker.get('status') or 'unknown'}",
            f"Agent id: {worker.get('agent_id') or 'none'}",
            f"Assignment: {worker.get('assignment_path') or 'unknown'}",
            f"Output: {worker.get('output_path') or 'unknown'}",
        ]
        worktree_path = summary.get("execution", {}).get("worktree_path")
        if worktree_path:
            lines.append(f"Suggested workdir: {worktree_path}")
        if summary.get("assignment_text") is not None:
            lines.extend(["Assignment text:", summary.get("assignment_text") or "[missing]"])
        if summary.get("output_text") is not None:
            lines.extend(["Output text:", summary.get("output_text") or "[missing]"])
        return "\n".join(lines)

    lines = [
        f"Run: {summary['run_id']}",
        f"Workflow: {summary.get('workflow') or 'unknown'}",
        f"Owner skill: {summary.get('owner_skill') or 'unknown'}",
        f"Status: {summary.get('status') or 'unknown'}",
        f"Workers: {summary.get('worker_count', 0)}",
        f"Events: {summary.get('events_count', 0)}",
    ]

    execution = summary.get("execution", {})
    if execution.get("exists"):
        lines.append(
            "Execution: "
            f"{execution.get('execution_mode') or 'unknown'} "
            f"({execution.get('execution_status') or execution.get('status') or 'unknown'})"
        )
        if execution.get("worktree_path"):
            lines.append(f"Worktree path: {execution['worktree_path']}")
    elif execution.get("status") != "missing":
        lines.append(f"Execution metadata: {execution.get('status') or 'unknown'}")

    worker_statuses = summary.get("worker_statuses", {})
    if worker_statuses:
        lines.append("Worker statuses:")
        for worker_id, status in worker_statuses.items():
            lines.append(f"- {worker_id}: {status}")

    validation_errors = summary.get("validation_errors", [])
    if validation_errors:
        lines.append("Validation errors:")
        lines.extend(f"- {error}" for error in validation_errors)

    execution_errors = execution.get("errors", [])
    if execution_errors:
        lines.append("Execution errors:")
        lines.extend(f"- {error}" for error in execution_errors)

    missing_outputs = summary.get("missing_outputs", [])
    missing_assignments = summary.get("missing_assignments", [])
    if missing_assignments:
        lines.append("Missing assignments:")
        lines.extend(f"- {worker_id}" for worker_id in missing_assignments)

    if missing_outputs:
        lines.append("Missing outputs:")
        lines.extend(f"- {worker_id}" for worker_id in missing_outputs)

    return "\n".join(lines)


def require_args(args: argparse.Namespace, *names: str) -> None:
    missing = [name for name in names if not getattr(args, name)]
    if missing:
        raise SystemExit("Missing required arguments: " + ", ".join(missing))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage repo-local team orchestration state.")
    parser.add_argument("--repo", type=str, default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--run-id", type=str, default=None)

    parser.add_argument("--init-run", action="store_true")
    parser.add_argument("--workflow", type=str, default=None)
    parser.add_argument("--goal", type=str, default=None)
    parser.add_argument("--owner-skill", type=str, default=None)
    parser.add_argument("--mode", type=str, default="team")

    parser.add_argument("--list-runs", action="store_true")
    parser.add_argument("--show-run", action="store_true")
    parser.add_argument("--show-worker", action="store_true")
    parser.add_argument("--include-assignment", action="store_true")
    parser.add_argument("--include-output", action="store_true")

    parser.add_argument("--add-worker", action="store_true")
    parser.add_argument("--worker-id", type=str, default=None)
    parser.add_argument("--role", type=str, default=None)
    parser.add_argument("--responsibility", type=str, default=None)
    parser.add_argument("--depends-on", type=str, default=None)
    parser.add_argument("--assignment-text", type=str, default=None)
    parser.add_argument("--assignment-file", type=str, default=None)

    parser.add_argument("--set-worker-status", action="store_true")
    parser.add_argument("--set-run-status", action="store_true")
    parser.add_argument("--status", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--agent-id", type=str, default=None)

    parser.add_argument("--write-output", action="store_true")
    parser.add_argument("--output-text", type=str, default=None)
    parser.add_argument("--output-file", type=str, default=None)
    parser.add_argument("--summary", type=str, default=None)
    parser.add_argument("--confidence", type=float, default=None)

    parser.add_argument("--append-event-json", type=str, default=None)
    parser.add_argument("--append-event-file", type=str, default=None)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = Path(args.repo).expanduser()
    ensure_state_files(base_dir)

    action_flags = {
        "init_run": args.init_run,
        "list_runs": args.list_runs,
        "show_run": args.show_run,
        "show_worker": args.show_worker,
        "add_worker": args.add_worker,
        "set_worker_status": args.set_worker_status,
        "set_run_status": args.set_run_status,
        "write_output": args.write_output,
        "append_event": bool(args.append_event_json or args.append_event_file),
    }
    selected_actions = [name for name, enabled in action_flags.items() if enabled]
    if len(selected_actions) != 1:
        raise SystemExit("Exactly one action must be requested.")

    action = selected_actions[0]

    if action == "init_run":
        require_args(args, "workflow", "goal", "owner_skill")
        result = init_run(
            base_dir=base_dir,
            workflow=args.workflow,
            goal=args.goal,
            owner_skill=args.owner_skill,
            run_id=args.run_id,
            mode=args.mode,
        )
    elif action == "list_runs":
        result = {"runs": list_runs(base_dir)}
    elif action == "show_run":
        require_args(args, "run_id")
        result = build_run_summary(base_dir, args.run_id)
    elif action == "show_worker":
        require_args(args, "run_id", "worker_id")
        result = build_worker_summary(
            base_dir=base_dir,
            run_id=args.run_id,
            worker_id=args.worker_id,
            include_assignment=args.include_assignment,
            include_output=args.include_output,
        )
    elif action == "add_worker":
        require_args(args, "run_id", "worker_id", "role")
        assignment_text = load_text_argument(args.assignment_text, args.assignment_file)
        result = add_worker(
            base_dir=base_dir,
            run_id=args.run_id,
            worker_id=args.worker_id,
            role=args.role,
            responsibility=args.responsibility,
            depends_on=normalize_list_argument(args.depends_on),
            assignment_text=assignment_text or None,
        )
    elif action == "set_worker_status":
        require_args(args, "run_id", "worker_id", "status")
        result = set_worker_status(
            base_dir=base_dir,
            run_id=args.run_id,
            worker_id=args.worker_id,
            status=args.status,
            reason=args.reason,
            agent_id=args.agent_id,
        )
    elif action == "set_run_status":
        require_args(args, "run_id", "status")
        result = set_run_status(
            base_dir=base_dir,
            run_id=args.run_id,
            status=args.status,
            reason=args.reason,
        )
    elif action == "write_output":
        require_args(args, "run_id", "worker_id")
        output_text = load_text_argument(args.output_text, args.output_file)
        if not output_text.strip():
            raise SystemExit("Worker output text is required.")
        result = write_worker_output(
            base_dir=base_dir,
            run_id=args.run_id,
            worker_id=args.worker_id,
            text=output_text,
            summary=args.summary,
            confidence=args.confidence,
        )
    elif action == "append_event":
        require_args(args, "run_id")
        payload = load_text_argument(args.append_event_json, args.append_event_file)
        if not payload.strip():
            raise SystemExit("Event payload is required.")
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise SystemExit("Event payload must decode to a JSON object.")
        event = append_team_event(base_dir=base_dir, run_id=args.run_id, event=parsed)
        result = {
            "action": "append_event",
            "run_id": args.run_id,
            "status": "ok",
            "event": event,
            "summary": build_run_summary(base_dir, args.run_id),
        }
    else:
        raise SystemExit(f"Unsupported action: {action}")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
