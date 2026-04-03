#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


STATE_DIRNAME = ".codex-workflows"
BACKUPS_DIRNAME = "backups"
TASK_STREAMS_DIRNAME = "task-streams"
TASK_STREAM_INDEX_FILENAME = "index.json"
SCHEMA_VERSION = 3
TASK_STREAM_SCHEMA_VERSION = 1
DEFAULT_STREAM_ID = "default"
DEFAULT_STREAM_STATE = "open"
VALID_STREAM_STATES = {"open", "paused", "closed"}
MEMORY_SECTIONS = (
    "Stable Facts",
    "Preferences",
    "Constraints",
    "Open Questions",
)
VERIFICATION_REQUIRED_KEYS = ("timestamp", "scope", "checks", "verdict")
VALID_VERDICTS = {"PASS", "FAIL", "PARTIAL"}

DEFAULT_POLICY: dict[str, Any] = {
    "meta": {
        "schema_version": SCHEMA_VERSION,
    },
    "task_loop": {
        "multi_file_threshold": 3,
        "stale_after_minutes": 45,
    },
    "verification": {
        "changed_lines_threshold": 80,
        "required_for_risk": ["medium", "high"],
    },
    "risk": {
        "high_if_files": 8,
        "medium_if_files": 3,
        "high_if_lines": 250,
        "medium_if_lines": 80,
    },
    "memory": {
        "refresh_after_scope_change": True,
    },
}

ACTIVE_LINE_RE = re.compile(r"^- \[[ xX]\]\s+Active:", re.IGNORECASE)
CHECKBOX_LINE_RE = re.compile(r"^- \[[ xX]\]\s+")
SECTION_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")
STREAM_ID_RE = re.compile(r"[^a-z0-9]+")
STREAM_PLACEHOLDER_TEXT = "No tasks recorded yet."
LEGACY_PLACEHOLDER_TEXT = "No active task loop yet."


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def now_timestamp() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def deep_merge(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(defaults)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_base_dir(base_dir: Path) -> Path:
    expanded = base_dir.expanduser()
    return expanded if expanded.is_dir() else expanded.parent


def find_git_root(base_dir: Path) -> Path | None:
    result = run_git(["rev-parse", "--show-toplevel"], base_dir)
    if result.returncode == 0:
        return Path(result.stdout.strip())

    for candidate in [base_dir, *base_dir.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def find_workspace_root(base_dir: Path) -> Path:
    normalized = normalize_base_dir(base_dir)
    repo_root = find_git_root(normalized)
    if repo_root is not None:
        return repo_root

    for candidate in [normalized, *normalized.parents]:
        if (candidate / STATE_DIRNAME).exists():
            return candidate
    return normalized


def get_state_dir(base_dir: Path) -> Path:
    return find_workspace_root(base_dir) / STATE_DIRNAME


def get_backup_dir(base_dir: Path) -> Path:
    return get_state_dir(base_dir) / BACKUPS_DIRNAME


def is_workflow_state_path(raw_path: str) -> bool:
    normalized = raw_path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized == STATE_DIRNAME or normalized.startswith(f"{STATE_DIRNAME}/")


def get_state_paths(base_dir: Path) -> dict[str, Path]:
    state_dir = get_state_dir(base_dir)
    task_streams_dir = state_dir / TASK_STREAMS_DIRNAME
    return {
        "state_dir": state_dir,
        "backups_dir": state_dir / BACKUPS_DIRNAME,
        "readme": state_dir / "README.md",
        "memory": state_dir / "memory.md",
        "task_loop": state_dir / "active-task-loop.md",
        "verification_log": state_dir / "verification-log.jsonl",
        "policy": state_dir / "policy.json",
        "task_streams_dir": task_streams_dir,
        "task_stream_index": task_streams_dir / TASK_STREAM_INDEX_FILENAME,
    }


def default_memory_sections() -> dict[str, list[str]]:
    return {
        "Stable Facts": ["- No durable project facts recorded yet."],
        "Preferences": ["- No explicit workflow preferences recorded yet."],
        "Constraints": ["- No durable constraints recorded yet."],
        "Open Questions": ["- None."],
    }


def render_memory_text(section_map: dict[str, list[str]]) -> str:
    lines = ["# Project Memory", ""]
    defaults = default_memory_sections()
    for section in MEMORY_SECTIONS:
        lines.append(f"## {section}")
        body_lines = [line.rstrip() for line in section_map.get(section, []) if line.strip()]
        if not body_lines:
            body_lines = defaults[section]
        lines.extend(body_lines)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def default_memory_text() -> str:
    return render_memory_text(default_memory_sections())


def default_task_loop_text() -> str:
    return f"# Active Task Loop\nUpdated: {now_timestamp()}\n\n{LEGACY_PLACEHOLDER_TEXT}\n"


def default_readme_text() -> str:
    return (
        "# Codex Workflows State\n\n"
        "This directory stores repo-local workflow state for the "
        "`codex-coding-workflows` plugin.\n\n"
        "Task streams, when enabled, live under `task-streams/` and generate the "
        "inspectable `active-task-loop.md` summary.\n\n"
        "The `backups/` directory stores repair-time snapshots before malformed "
        "workflow files are rewritten.\n"
    )


def serialize_policy(policy: dict[str, Any]) -> str:
    return json.dumps(deep_merge(DEFAULT_POLICY, policy), indent=2) + "\n"


def ensure_state_files(base_dir: Path) -> dict[str, Path]:
    paths = get_state_paths(base_dir)
    state_dir = paths["state_dir"]
    state_dir.mkdir(parents=True, exist_ok=True)
    paths["backups_dir"].mkdir(parents=True, exist_ok=True)

    defaults = {
        "readme": default_readme_text(),
        "memory": default_memory_text(),
        "task_loop": default_task_loop_text(),
        "verification_log": "",
        "policy": serialize_policy(DEFAULT_POLICY),
    }
    for key, text in defaults.items():
        path = paths[key]
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    return paths


def load_policy(base_dir: Path) -> dict[str, Any]:
    path = get_state_paths(base_dir)["policy"]
    if not path.exists():
        return {
            "path": str(path),
            "data": copy.deepcopy(DEFAULT_POLICY),
            "status": "missing",
            "used_default": True,
            "errors": ["policy.json is missing; using defaults."],
        }

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "data": copy.deepcopy(DEFAULT_POLICY),
            "status": "invalid",
            "used_default": True,
            "errors": [f"policy.json is invalid; using defaults ({exc})."],
        }

    if not isinstance(raw, dict):
        return {
            "path": str(path),
            "data": copy.deepcopy(DEFAULT_POLICY),
            "status": "invalid",
            "used_default": True,
            "errors": ["policy.json must contain a JSON object; using defaults."],
        }

    merged = deep_merge(DEFAULT_POLICY, raw)
    status = "healthy"
    errors: list[str] = []
    if raw.get("meta", {}).get("schema_version") != SCHEMA_VERSION:
        status = "invalid"
        errors.append(
            f"policy.json schema_version is {raw.get('meta', {}).get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}."
        )

    return {
        "path": str(path),
        "data": merged,
        "status": status,
        "used_default": False,
        "errors": errors,
    }


def parse_memory_sections(markdown_text: str) -> dict[str, list[str]]:
    sections = {section: [] for section in MEMORY_SECTIONS}
    current: str | None = None

    for line in markdown_text.splitlines():
        header_match = SECTION_HEADER_RE.match(line.strip())
        if header_match:
            heading = header_match.group(1)
            current = heading if heading in sections else None
            continue
        if current is not None:
            sections[current].append(line.rstrip())

    return sections


def normalize_memory_text(raw_text: str) -> str:
    if not raw_text.strip():
        return default_memory_text()

    parsed = parse_memory_sections(raw_text)
    defaults = default_memory_sections()
    cleaned: dict[str, list[str]] = {}

    for section in MEMORY_SECTIONS:
        lines = [line for line in parsed.get(section, []) if line.strip()]
        cleaned[section] = lines or defaults[section]

    return render_memory_text(cleaned)


def get_memory_status(base_dir: Path) -> dict[str, Any]:
    path = get_state_paths(base_dir)["memory"]
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "status": "missing",
            "missing_sections": list(MEMORY_SECTIONS),
            "reasons": ["memory.md is missing."],
        }

    parsed = parse_memory_sections(path.read_text(encoding="utf-8"))
    missing_sections = [
        section
        for section in MEMORY_SECTIONS
        if not any(line.strip() for line in parsed.get(section, []))
    ]
    return {
        "path": str(path),
        "exists": True,
        "status": "healthy" if not missing_sections else "invalid",
        "missing_sections": missing_sections,
        "reasons": [] if not missing_sections else [
            "memory.md is missing required sections or section content."
        ],
    }


def extract_updated_at(markdown_text: str) -> str | None:
    for line in markdown_text.splitlines():
        if line.startswith("Updated:"):
            return line.split(":", 1)[1].strip()
    return None


def normalize_task_item(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if CHECKBOX_LINE_RE.match(stripped):
        return stripped
    return f"- [ ] {stripped}"


def promote_to_active(line: str) -> str:
    normalized = normalize_task_item(line)
    body = CHECKBOX_LINE_RE.sub("", normalized).strip()
    if body.lower().startswith("active:") or body.lower().startswith("pending:"):
        body = body.split(":", 1)[1].strip()
    return f"- [ ] Active: {body}"


def demote_to_pending(line: str) -> str:
    normalized = normalize_task_item(line)
    body = CHECKBOX_LINE_RE.sub("", normalized).strip()
    if body.lower().startswith("active:") or body.lower().startswith("pending:"):
        body = body.split(":", 1)[1].strip()
    return f"- [ ] Pending: {body}"


def extract_legacy_task_lines(markdown_text: str) -> tuple[list[str], bool]:
    task_lines: list[str] = []
    placeholder = False
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("# ") or line.startswith("Updated:"):
            continue
        if line == LEGACY_PLACEHOLDER_TEXT:
            placeholder = True
            continue
        task_lines.append(normalize_task_item(line))
    return task_lines, placeholder


def normalize_open_task_lines(task_lines: list[str]) -> list[str]:
    cleaned = [normalize_task_item(line) for line in task_lines if normalize_task_item(line)]
    if not cleaned:
        return []
    active_indices = [index for index, line in enumerate(cleaned) if ACTIVE_LINE_RE.match(line)]
    if not active_indices:
        cleaned[0] = promote_to_active(cleaned[0])
        return cleaned
    first_active = active_indices[0]
    cleaned[first_active] = promote_to_active(cleaned[first_active])
    for index in active_indices[1:]:
        cleaned[index] = demote_to_pending(cleaned[index])
    return cleaned


def normalize_paused_task_lines(task_lines: list[str]) -> list[str]:
    cleaned = [normalize_task_item(line) for line in task_lines if normalize_task_item(line)]
    return [demote_to_pending(line) for line in cleaned]


def normalize_task_loop_text(raw_text: str) -> str:
    task_lines, _ = extract_legacy_task_lines(raw_text)
    normalized = normalize_open_task_lines(task_lines)
    body = "\n".join(normalized) if normalized else LEGACY_PLACEHOLDER_TEXT
    return f"# Active Task Loop\nUpdated: {now_timestamp()}\n\n{body}\n"


def normalize_stream_id(raw_value: str | None) -> str:
    candidate = str(raw_value or "").strip().lower()
    candidate = STREAM_ID_RE.sub("-", candidate).strip("-")
    return candidate or DEFAULT_STREAM_ID


def default_stream_title(stream_id: str) -> str:
    parts = [part for part in normalize_stream_id(stream_id).split("-") if part]
    return " ".join(part.capitalize() for part in parts) or "Default"


def normalize_stream_state(raw_value: str | None) -> str:
    candidate = str(raw_value or DEFAULT_STREAM_STATE).strip().lower()
    return candidate if candidate in VALID_STREAM_STATES else DEFAULT_STREAM_STATE


def default_task_stream_index(
    stream_ids: list[str] | None = None,
    primary_stream_id: str | None = None,
) -> dict[str, Any]:
    normalized_ids = [normalize_stream_id(stream_id) for stream_id in stream_ids or []]
    primary = normalize_stream_id(primary_stream_id) if primary_stream_id else None
    if primary is None and normalized_ids:
        primary = normalized_ids[0]
    return {
        "schema_version": TASK_STREAM_SCHEMA_VERSION,
        "primary_stream_id": primary,
        "streams": normalized_ids,
    }


def serialize_task_stream_index(index_data: dict[str, Any]) -> str:
    return json.dumps(index_data, indent=2) + "\n"


def get_task_stream_path(base_dir: Path, stream_id: str) -> Path:
    paths = get_state_paths(base_dir)
    return paths["task_streams_dir"] / f"{normalize_stream_id(stream_id)}.md"


def parse_task_stream_text(markdown_text: str) -> dict[str, Any]:
    stream_id: str | None = None
    title: str | None = None
    state: str | None = None
    updated_at: str | None = None
    task_lines: list[str] = []
    placeholder = False

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("# "):
            continue
        if line.startswith("ID:"):
            stream_id = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Title:"):
            title = line.split(":", 1)[1].strip()
            continue
        if line.startswith("State:"):
            state = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Updated:"):
            updated_at = line.split(":", 1)[1].strip()
            continue
        if line in {STREAM_PLACEHOLDER_TEXT, LEGACY_PLACEHOLDER_TEXT}:
            placeholder = True
            continue
        task_lines.append(normalize_task_item(line))

    return {
        "id": stream_id,
        "title": title,
        "state": state,
        "updated_at": updated_at,
        "task_lines": task_lines,
        "placeholder": placeholder,
    }


def render_task_stream_text(
    stream_id: str,
    title: str,
    state: str,
    task_lines: list[str],
) -> str:
    body = "\n".join(task_lines) if task_lines else STREAM_PLACEHOLDER_TEXT
    return (
        "# Task Stream\n"
        f"ID: {normalize_stream_id(stream_id)}\n"
        f"Title: {title.strip() or default_stream_title(stream_id)}\n"
        f"State: {normalize_stream_state(state)}\n"
        f"Updated: {now_timestamp()}\n\n"
        f"{body}\n"
    )


def default_task_stream_text(
    stream_id: str = DEFAULT_STREAM_ID,
    title: str | None = None,
    state: str = DEFAULT_STREAM_STATE,
) -> str:
    return render_task_stream_text(
        stream_id=normalize_stream_id(stream_id),
        title=(title or default_stream_title(stream_id)).strip(),
        state=normalize_stream_state(state),
        task_lines=[],
    )


def normalize_task_stream_text(
    raw_text: str,
    stream_id: str,
    title: str | None = None,
    state: str | None = None,
) -> str:
    parsed = parse_task_stream_text(raw_text)
    normalized_stream_id = normalize_stream_id(stream_id or parsed["id"])
    normalized_title = (title or parsed["title"] or default_stream_title(normalized_stream_id)).strip()
    normalized_state = normalize_stream_state(state or parsed["state"] or DEFAULT_STREAM_STATE)
    task_lines = parsed["task_lines"]
    if normalized_state == "open":
        task_lines = normalize_open_task_lines(task_lines)
    else:
        task_lines = normalize_paused_task_lines(task_lines)
    return render_task_stream_text(
        stream_id=normalized_stream_id,
        title=normalized_title,
        state=normalized_state,
        task_lines=task_lines,
    )


def load_task_stream_index(base_dir: Path) -> dict[str, Any]:
    paths = get_state_paths(base_dir)
    index_path = paths["task_stream_index"]
    streams_dir = paths["task_streams_dir"]

    dangling_stream_files = (
        sorted(path.name for path in streams_dir.glob("*.md"))
        if streams_dir.exists()
        else []
    )
    if not index_path.exists():
        if dangling_stream_files:
            return {
                "path": str(index_path),
                "exists": False,
                "status": "invalid",
                "data": None,
                "errors": [
                    "task-streams/index.json is missing while task stream files exist."
                ],
            }
        return {
            "path": str(index_path),
            "exists": False,
            "status": "missing",
            "data": None,
            "errors": [],
        }

    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(index_path),
            "exists": True,
            "status": "invalid",
            "data": None,
            "errors": [f"task-streams/index.json is invalid ({exc})."],
        }

    if not isinstance(raw, dict):
        return {
            "path": str(index_path),
            "exists": True,
            "status": "invalid",
            "data": None,
            "errors": ["task-streams/index.json must contain a JSON object."],
        }

    errors: list[str] = []
    if raw.get("schema_version") != TASK_STREAM_SCHEMA_VERSION:
        errors.append(
            "task-streams/index.json has an unexpected schema_version."
        )

    raw_streams = raw.get("streams")
    if not isinstance(raw_streams, list) or not raw_streams:
        errors.append("task-streams/index.json must declare a non-empty streams list.")
        raw_streams = []

    normalized_streams: list[str] = []
    seen_stream_ids: set[str] = set()
    for stream_id in raw_streams:
        if not isinstance(stream_id, str):
            errors.append("task-streams/index.json streams must be strings.")
            continue
        normalized_stream_id = normalize_stream_id(stream_id)
        if normalized_stream_id != stream_id:
            errors.append(
                f"Task stream id {stream_id!r} must already be normalized as {normalized_stream_id!r}."
            )
        if normalized_stream_id in seen_stream_ids:
            errors.append(f"Duplicate task stream id {normalized_stream_id!r} in index.")
            continue
        normalized_streams.append(normalized_stream_id)
        seen_stream_ids.add(normalized_stream_id)

    primary_stream_id = raw.get("primary_stream_id")
    if not isinstance(primary_stream_id, str):
        errors.append("task-streams/index.json primary_stream_id must be a string.")
        primary_stream_id = None
    else:
        normalized_primary_stream_id = normalize_stream_id(primary_stream_id)
        if normalized_primary_stream_id != primary_stream_id:
            errors.append(
                "task-streams/index.json primary_stream_id must already be normalized."
            )
        primary_stream_id = normalized_primary_stream_id

    if primary_stream_id and primary_stream_id not in normalized_streams:
        errors.append("task-streams/index.json primary_stream_id must refer to a declared stream.")

    status = "healthy" if not errors else "invalid"
    return {
        "path": str(index_path),
        "exists": True,
        "status": status,
        "data": default_task_stream_index(
            stream_ids=normalized_streams,
            primary_stream_id=primary_stream_id,
        ),
        "errors": errors,
    }


def write_task_stream_index(base_dir: Path, index_data: dict[str, Any]) -> Path:
    paths = ensure_state_files(base_dir)
    paths["task_streams_dir"].mkdir(parents=True, exist_ok=True)
    normalized = default_task_stream_index(
        stream_ids=index_data.get("streams", []),
        primary_stream_id=index_data.get("primary_stream_id"),
    )
    paths["task_stream_index"].write_text(
        serialize_task_stream_index(normalized),
        encoding="utf-8",
    )
    return paths["task_stream_index"]


def get_legacy_task_loop_status(
    base_dir: Path,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = get_state_paths(base_dir)["task_loop"]
    policy_data = policy or load_policy(base_dir)["data"]
    stale_after = int(policy_data["task_loop"]["stale_after_minutes"])

    if not path.exists():
        return {
            "mode": "legacy",
            "path": str(path),
            "exists": False,
            "status": "missing",
            "updated_at": None,
            "active_step_count": 0,
            "stale": False,
            "placeholder": True,
            "reasons": ["active-task-loop.md is missing."],
            "primary_stream_id": None,
            "stream_count": 0,
            "open_stream_count": 0,
            "streams": [],
        }

    text = path.read_text(encoding="utf-8")
    updated_at = extract_updated_at(text)
    updated_dt = parse_timestamp(updated_at)
    task_lines, placeholder = extract_legacy_task_lines(text)
    active_step_count = sum(1 for line in task_lines if ACTIVE_LINE_RE.match(line))
    reasons: list[str] = []

    if placeholder:
        status = "missing"
        stale = False
        reasons.append("Task loop has not been initialized with a real active step.")
    elif updated_dt is None:
        status = "invalid"
        stale = True
        reasons.append("Task loop timestamp is missing or invalid.")
    elif active_step_count != 1:
        status = "invalid"
        stale = True
        reasons.append(
            f"Task loop must have exactly one active step, found {active_step_count}."
        )
    elif updated_dt < datetime.now().astimezone() - timedelta(minutes=stale_after):
        status = "stale"
        stale = True
        reasons.append("Task loop timestamp is older than the configured freshness threshold.")
    else:
        status = "healthy"
        stale = False

    streams = []
    if not placeholder:
        streams.append(
            {
                "id": DEFAULT_STREAM_ID,
                "title": default_stream_title(DEFAULT_STREAM_ID),
                "state": "open",
                "path": str(path),
                "exists": True,
                "status": status,
                "updated_at": updated_at,
                "active_step_count": active_step_count,
                "stale": stale,
                "placeholder": placeholder,
                "reasons": list(reasons),
                "is_primary": True,
                "task_lines": task_lines,
            }
        )

    return {
        "mode": "legacy",
        "path": str(path),
        "exists": True,
        "status": status,
        "updated_at": updated_at,
        "active_step_count": active_step_count,
        "stale": stale,
        "placeholder": placeholder,
        "reasons": reasons,
        "primary_stream_id": DEFAULT_STREAM_ID if streams else None,
        "stream_count": len(streams),
        "open_stream_count": len(streams),
        "streams": streams,
    }


def get_task_stream_status(
    base_dir: Path,
    stream_id: str,
    policy: dict[str, Any],
    is_primary: bool = False,
) -> dict[str, Any]:
    normalized_stream_id = normalize_stream_id(stream_id)
    path = get_task_stream_path(base_dir, normalized_stream_id)
    stale_after = int(policy["task_loop"]["stale_after_minutes"])

    if not path.exists():
        return {
            "id": normalized_stream_id,
            "title": default_stream_title(normalized_stream_id),
            "state": DEFAULT_STREAM_STATE,
            "path": str(path),
            "exists": False,
            "status": "invalid",
            "updated_at": None,
            "active_step_count": 0,
            "stale": True,
            "placeholder": True,
            "reasons": ["Task stream file is missing."],
            "is_primary": is_primary,
            "task_lines": [],
        }

    parsed = parse_task_stream_text(path.read_text(encoding="utf-8"))
    title = (parsed["title"] or default_stream_title(normalized_stream_id)).strip()
    state = normalize_stream_state(parsed["state"])
    updated_at = parsed["updated_at"]
    updated_dt = parse_timestamp(updated_at)
    task_lines = parsed["task_lines"]
    placeholder = parsed["placeholder"] or not task_lines
    active_step_count = sum(1 for line in task_lines if ACTIVE_LINE_RE.match(line))
    reasons: list[str] = []

    if parsed["id"] is None:
        reasons.append("Task stream file is missing an ID header.")
    elif normalize_stream_id(parsed["id"]) != normalized_stream_id:
        reasons.append("Task stream file ID does not match the stream declared in the index.")

    if parsed["state"] is None:
        reasons.append("Task stream file is missing a State header.")
    elif parsed["state"].strip().lower() not in VALID_STREAM_STATES:
        reasons.append("Task stream file has an invalid State header.")

    if updated_dt is None:
        reasons.append("Task stream timestamp is missing or invalid.")

    if reasons:
        status = "invalid"
        stale = True
    elif state == "open":
        if placeholder:
            status = "missing"
            stale = False
            reasons.append("Open task stream has not been initialized with a real active step.")
        elif active_step_count != 1:
            status = "invalid"
            stale = True
            reasons.append(
                f"Open task stream must have exactly one active step, found {active_step_count}."
            )
        elif updated_dt < datetime.now().astimezone() - timedelta(minutes=stale_after):
            status = "stale"
            stale = True
            reasons.append(
                "Task stream timestamp is older than the configured freshness threshold."
            )
        else:
            status = "healthy"
            stale = False
    else:
        if active_step_count != 0:
            status = "invalid"
            stale = True
            reasons.append(
                f"{state.capitalize()} task stream must not have active steps, found {active_step_count}."
            )
        else:
            status = "healthy"
            stale = False

    return {
        "id": normalized_stream_id,
        "title": title,
        "state": state,
        "path": str(path),
        "exists": True,
        "status": status,
        "updated_at": updated_at,
        "active_step_count": active_step_count,
        "stale": stale,
        "placeholder": placeholder,
        "reasons": reasons,
        "is_primary": is_primary,
        "task_lines": task_lines,
    }


def render_task_state_summary(task_state: dict[str, Any]) -> str:
    streams = task_state.get("streams", [])
    lines = [
        "# Active Task Loop",
        f"Updated: {task_state.get('updated_at') or now_timestamp()}",
        "",
        "Mode: streams",
        f"Primary stream: {task_state.get('primary_stream_id') or 'none'}",
        "",
    ]

    if not streams:
        lines.append("No task streams configured.")
        lines.append("")
        return "\n".join(lines)

    for stream in streams:
        badges = [stream["id"]]
        if stream.get("is_primary"):
            badges.append("primary")
        badges.append(stream["state"])
        badges.append(stream["status"])
        lines.append(f"## {stream['title']} ({', '.join(badges)})")
        lines.append(f"Updated: {stream.get('updated_at') or 'unknown'}")
        lines.append("")
        if stream["task_lines"]:
            lines.extend(stream["task_lines"])
        else:
            lines.append(STREAM_PLACEHOLDER_TEXT)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_task_state_summary(base_dir: Path, task_state: dict[str, Any] | None = None) -> Path:
    paths = ensure_state_files(base_dir)
    summary = task_state or get_task_state(base_dir)
    if summary.get("mode") == "streams":
        paths["task_loop"].write_text(
            render_task_state_summary(summary),
            encoding="utf-8",
        )
    return paths["task_loop"]


def get_task_state(
    base_dir: Path,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy_data = policy or load_policy(base_dir)["data"]
    paths = get_state_paths(base_dir)
    index_info = load_task_stream_index(base_dir)
    if index_info["status"] == "missing":
        return get_legacy_task_loop_status(base_dir, policy_data)

    if index_info["status"] == "invalid":
        summary_path = paths["task_loop"]
        summary_updated_at = (
            extract_updated_at(summary_path.read_text(encoding="utf-8"))
            if summary_path.exists()
            else None
        )
        return {
            "mode": "streams",
            "path": str(summary_path),
            "exists": summary_path.exists() or index_info["exists"],
            "status": "invalid",
            "updated_at": summary_updated_at,
            "active_step_count": 0,
            "stale": True,
            "placeholder": False,
            "reasons": list(index_info["errors"]),
            "primary_stream_id": None,
            "stream_count": 0,
            "open_stream_count": 0,
            "streams": [],
            "index_path": index_info["path"],
        }

    index_data = index_info["data"]
    primary_stream_id = index_data["primary_stream_id"]
    streams = [
        get_task_stream_status(
            base_dir,
            stream_id=stream_id,
            policy=policy_data,
            is_primary=stream_id == primary_stream_id,
        )
        for stream_id in index_data["streams"]
    ]
    open_streams = [stream for stream in streams if stream["state"] == "open"]
    latest_stream_update: datetime | None = None
    latest_updated_at: str | None = None
    for stream in streams:
        updated_dt = parse_timestamp(stream["updated_at"])
        if updated_dt is None:
            continue
        if latest_stream_update is None or updated_dt > latest_stream_update:
            latest_stream_update = updated_dt
            latest_updated_at = stream["updated_at"]

    reasons: list[str] = []
    invalid_streams = [stream for stream in streams if stream["status"] == "invalid"]
    missing_open_streams = [stream for stream in open_streams if stream["status"] == "missing"]
    stale_open_streams = [stream for stream in open_streams if stream["status"] == "stale"]
    if invalid_streams:
        for stream in invalid_streams:
            reasons.extend(f"{stream['id']}: {reason}" for reason in stream["reasons"])
        status = "invalid"
        stale = True
    elif not streams:
        status = "missing"
        stale = False
        reasons.append("No task streams are declared in the index.")
    elif not open_streams:
        status = "missing"
        stale = False
        reasons.append("No open task streams are available for active work.")
    elif missing_open_streams:
        status = "missing"
        stale = False
        for stream in missing_open_streams:
            reasons.extend(f"{stream['id']}: {reason}" for reason in stream["reasons"])
    elif stale_open_streams:
        status = "stale"
        stale = True
        for stream in stale_open_streams:
            reasons.extend(f"{stream['id']}: {reason}" for reason in stream["reasons"])
    else:
        status = "healthy"
        stale = False

    placeholder = not open_streams or all(stream["placeholder"] for stream in open_streams)
    return {
        "mode": "streams",
        "path": str(paths["task_loop"]),
        "exists": True,
        "status": status,
        "updated_at": latest_updated_at,
        "active_step_count": sum(stream["active_step_count"] for stream in streams),
        "stale": stale,
        "placeholder": placeholder,
        "reasons": reasons,
        "primary_stream_id": primary_stream_id,
        "stream_count": len(streams),
        "open_stream_count": len(open_streams),
        "streams": streams,
        "index_path": index_info["path"],
    }


def get_task_loop_status(base_dir: Path, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    task_state = get_task_state(base_dir, policy)
    return {
        "mode": task_state["mode"],
        "path": task_state["path"],
        "exists": task_state["exists"],
        "status": task_state["status"],
        "updated_at": task_state["updated_at"],
        "active_step_count": task_state["active_step_count"],
        "stale": task_state["stale"],
        "placeholder": task_state["placeholder"],
        "reasons": task_state["reasons"],
        "stream_count": task_state.get("stream_count", 0),
        "open_stream_count": task_state.get("open_stream_count", 0),
        "primary_stream_id": task_state.get("primary_stream_id"),
    }


def ensure_task_stream_mode(
    base_dir: Path,
    initial_stream_id: str | None = None,
    initial_title: str | None = None,
    initial_state: str | None = None,
) -> dict[str, Any]:
    ensure_state_files(base_dir)
    index_info = load_task_stream_index(base_dir)
    if index_info["status"] == "invalid":
        raise ValueError("Task stream state is invalid. Repair it before enabling stream mode.")
    if index_info["status"] == "healthy":
        return index_info["data"]

    paths = get_state_paths(base_dir)
    paths["task_streams_dir"].mkdir(parents=True, exist_ok=True)
    stream_ids: list[str] = []
    primary_stream_id: str | None = None

    legacy_status = get_legacy_task_loop_status(base_dir)
    if legacy_status["status"] != "missing" and not legacy_status["placeholder"]:
        legacy_text = paths["task_loop"].read_text(encoding="utf-8")
        legacy_stream_id = DEFAULT_STREAM_ID
        get_task_stream_path(base_dir, legacy_stream_id).write_text(
            normalize_task_stream_text(
                legacy_text,
                stream_id=legacy_stream_id,
                title=default_stream_title(legacy_stream_id),
                state="open",
            ),
            encoding="utf-8",
        )
        stream_ids.append(legacy_stream_id)
        primary_stream_id = legacy_stream_id

    requested_stream_id = normalize_stream_id(initial_stream_id)
    if requested_stream_id not in stream_ids:
        stream_ids.append(requested_stream_id)
        get_task_stream_path(base_dir, requested_stream_id).write_text(
            default_task_stream_text(
                stream_id=requested_stream_id,
                title=initial_title or default_stream_title(requested_stream_id),
                state=initial_state or DEFAULT_STREAM_STATE,
            ),
            encoding="utf-8",
        )
        if primary_stream_id is None:
            primary_stream_id = requested_stream_id

    index_data = default_task_stream_index(
        stream_ids=stream_ids,
        primary_stream_id=primary_stream_id,
    )
    write_task_stream_index(base_dir, index_data)
    write_task_state_summary(base_dir, get_task_state(base_dir))
    return index_data


def ensure_task_stream(
    base_dir: Path,
    stream_id: str,
    title: str | None = None,
    state: str | None = None,
    make_primary: bool = False,
) -> Path:
    normalized_stream_id = normalize_stream_id(stream_id)
    index_data = ensure_task_stream_mode(
        base_dir,
        initial_stream_id=normalized_stream_id,
        initial_title=title,
        initial_state=state,
    )
    if normalized_stream_id not in index_data["streams"]:
        index_data["streams"].append(normalized_stream_id)

    stream_path = get_task_stream_path(base_dir, normalized_stream_id)
    current = stream_path.read_text(encoding="utf-8") if stream_path.exists() else ""
    stream_path.write_text(
        normalize_task_stream_text(
            current,
            stream_id=normalized_stream_id,
            title=title,
            state=state,
        ),
        encoding="utf-8",
    )
    if make_primary or not index_data.get("primary_stream_id"):
        index_data["primary_stream_id"] = normalized_stream_id
    write_task_stream_index(base_dir, index_data)
    write_task_state_summary(base_dir, get_task_state(base_dir))
    return stream_path


def set_primary_task_stream(base_dir: Path, stream_id: str) -> Path:
    normalized_stream_id = normalize_stream_id(stream_id)
    stream_path = ensure_task_stream(base_dir, normalized_stream_id)
    index_data = load_task_stream_index(base_dir)["data"]
    index_data["primary_stream_id"] = normalized_stream_id
    write_task_stream_index(base_dir, index_data)
    write_task_state_summary(base_dir, get_task_state(base_dir))
    return stream_path


def list_task_streams(base_dir: Path, policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return get_task_state(base_dir, policy).get("streams", [])


def update_task_loop(
    base_dir: Path,
    raw_text: str,
    stream_id: str | None = None,
    stream_title: str | None = None,
    stream_state: str | None = None,
    set_primary: bool = False,
) -> Path:
    if stream_id is None and load_task_stream_index(base_dir)["status"] == "missing":
        path = ensure_state_files(base_dir)["task_loop"]
        path.write_text(normalize_task_loop_text(raw_text), encoding="utf-8")
        return path

    if stream_id is None:
        current_task_state = get_task_state(base_dir)
        target_stream_id = current_task_state.get("primary_stream_id") or DEFAULT_STREAM_ID
    else:
        target_stream_id = normalize_stream_id(stream_id)

    ensure_task_stream(
        base_dir,
        target_stream_id,
        title=stream_title,
        state=stream_state,
        make_primary=set_primary,
    )
    stream_path = get_task_stream_path(base_dir, target_stream_id)
    current = stream_path.read_text(encoding="utf-8") if stream_path.exists() else ""
    seed_text = raw_text if raw_text.strip() else current
    stream_path.write_text(
        normalize_task_stream_text(
            seed_text,
            stream_id=target_stream_id,
            title=stream_title,
            state=stream_state,
        ),
        encoding="utf-8",
    )
    if set_primary:
        set_primary_task_stream(base_dir, target_stream_id)
    else:
        write_task_state_summary(base_dir, get_task_state(base_dir))
    return stream_path


def load_verification_entries(base_dir: Path) -> dict[str, Any]:
    path = get_state_paths(base_dir)["verification_log"]
    if not path.exists():
        return {
            "path": str(path),
            "entries": [],
            "invalid_lines": 0,
            "invalid_reasons": [],
        }

    entries: list[dict[str, Any]] = []
    invalid_lines = 0
    invalid_reasons: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
            invalid_reasons.append(f"Line {line_number}: entry must decode to a JSON object.")
            continue

        entry_reasons = validate_verification_entry(parsed)
        if entry_reasons:
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: " + "; ".join(entry_reasons))
            continue
        entries.append(parsed)
    return {
        "path": str(path),
        "entries": entries,
        "invalid_lines": invalid_lines,
        "invalid_reasons": invalid_reasons,
    }


def validate_verification_entry(entry: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in VERIFICATION_REQUIRED_KEYS:
        if key not in entry:
            reasons.append(f"Missing required key: {key}")

    timestamp = entry.get("timestamp")
    if timestamp is not None and parse_timestamp(str(timestamp)) is None:
        reasons.append("timestamp is not a valid ISO 8601 value.")
    if "scope" in entry and not isinstance(entry["scope"], dict):
        reasons.append("scope must be a JSON object.")
    if "checks" in entry and not isinstance(entry["checks"], list):
        reasons.append("checks must be a JSON array.")
    if "verdict" in entry and str(entry["verdict"]).upper() not in VALID_VERDICTS:
        reasons.append("verdict must be PASS, FAIL, or PARTIAL.")
    if "stream_id" in entry and (
        not isinstance(entry["stream_id"], str)
        or normalize_stream_id(entry["stream_id"]) != entry["stream_id"]
    ):
        reasons.append("stream_id must be a normalized non-empty string when present.")
    return reasons


def get_latest_verification_entry(
    entries: list[dict[str, Any]],
    stream_id: str | None = None,
) -> dict[str, Any] | None:
    latest_entry: dict[str, Any] | None = None
    latest_dt: datetime | None = None
    normalized_stream_id = normalize_stream_id(stream_id) if stream_id else None
    for entry in entries:
        entry_stream_id = entry.get("stream_id")
        if normalized_stream_id is not None and entry_stream_id not in {None, normalized_stream_id}:
            continue
        entry_dt = parse_timestamp(str(entry.get("timestamp")))
        if entry_dt is None:
            continue
        if latest_dt is None or entry_dt > latest_dt:
            latest_dt = entry_dt
            latest_entry = entry
    return latest_entry


def get_verification_state(
    base_dir: Path,
    task_loop_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded = load_verification_entries(base_dir)
    entries = loaded["entries"]
    latest_entry = get_latest_verification_entry(entries)
    latest_at = parse_timestamp((latest_entry or {}).get("timestamp"))
    task_status = task_loop_status or get_task_state(base_dir)
    task_updated = parse_timestamp(task_status.get("updated_at"))
    reasons = list(loaded["invalid_reasons"])

    if loaded["invalid_lines"] > 0:
        status = "invalid"
    elif not entries:
        status = "missing"
    elif task_updated is not None and latest_at is not None and latest_at < task_updated:
        status = "stale"
        reasons.append("Latest verification entry predates the current task loop.")
    else:
        status = "present"

    return {
        "path": loaded["path"],
        "status": status,
        "entry_count": len(entries),
        "invalid_lines": loaded["invalid_lines"],
        "invalid_reasons": reasons,
        "latest_timestamp": (latest_entry or {}).get("timestamp"),
        "latest_verdict": (latest_entry or {}).get("verdict"),
    }


def classify_risk(
    changed_file_count: int,
    total_changed_lines: int,
    categories: dict[str, int],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if changed_file_count == 0:
        return {
            "risk_level": "low",
            "reasons": ["No changed files detected."],
        }

    code_like_changes = (
        categories.get("code", 0) + categories.get("config", 0) + categories.get("tests", 0)
    )
    docs_only = (
        categories.get("docs", 0) > 0
        and code_like_changes == 0
        and categories.get("other", 0) == 0
    )
    if docs_only:
        return {
            "risk_level": "low",
            "reasons": ["Docs-only change set."],
        }

    reasons: list[str] = []
    risk_config = policy["risk"]
    if changed_file_count >= int(risk_config["high_if_files"]):
        reasons.append(
            f"Changed file count ({changed_file_count}) meets the high-risk threshold."
        )
    if total_changed_lines >= int(risk_config["high_if_lines"]):
        reasons.append(
            f"Changed line count ({total_changed_lines}) meets the high-risk threshold."
        )
    if reasons:
        return {"risk_level": "high", "reasons": reasons}

    if changed_file_count >= int(risk_config["medium_if_files"]):
        reasons.append(
            f"Changed file count ({changed_file_count}) meets the medium-risk threshold."
        )
    if total_changed_lines >= int(risk_config["medium_if_lines"]):
        reasons.append(
            f"Changed line count ({total_changed_lines}) meets the medium-risk threshold."
        )
    if code_like_changes:
        reasons.append("Change set includes code, config, or test files.")
    return {
        "risk_level": "medium" if reasons else "low",
        "reasons": reasons or ["Small change set below configured risk thresholds."],
    }


def verification_required_for(risk_level: str, policy: dict[str, Any]) -> bool:
    return risk_level in set(policy["verification"]["required_for_risk"])


def should_refresh_memory(
    changed_file_count: int,
    categories: dict[str, int],
    risk_level: str,
    policy: dict[str, Any],
) -> bool:
    if not policy["memory"].get("refresh_after_scope_change", False):
        return False
    if changed_file_count == 0:
        return False

    code_or_config = categories.get("code", 0) + categories.get("config", 0)
    if code_or_config == 0:
        return False

    return risk_level in {"medium", "high"} or changed_file_count >= int(
        policy["task_loop"]["multi_file_threshold"]
    )


def insert_bullet_in_section(markdown_text: str, section_name: str, bullet_text: str) -> str:
    normalized = normalize_memory_text(markdown_text)
    bullet = bullet_text.strip()
    if not bullet.startswith("- "):
        bullet = f"- {bullet}"
    if bullet in normalized:
        return normalized

    lines = normalized.splitlines()
    output: list[str] = []
    in_target = False
    inserted = False
    section_header = f"## {section_name}"

    for index, line in enumerate(lines):
        if line.strip() == section_header:
            in_target = True
            output.append(line)
            continue

        if in_target and line.startswith("## "):
            if not inserted:
                if output and output[-1] != "":
                    output.append("")
                output.append(bullet)
                inserted = True
            in_target = False

        output.append(line)

        is_last = index == len(lines) - 1
        if in_target and is_last and not inserted:
            if output and output[-1] != "":
                output.append("")
            output.append(bullet)
            inserted = True

    return "\n".join(output).rstrip() + "\n"


def append_memory_fact(base_dir: Path, fact: str) -> Path:
    path = ensure_state_files(base_dir)["memory"]
    current = path.read_text(encoding="utf-8")
    updated = insert_bullet_in_section(current, "Stable Facts", fact)
    path.write_text(updated, encoding="utf-8")
    return path


def append_verification_entry(base_dir: Path, entry: dict[str, Any]) -> Path:
    path = ensure_state_files(base_dir)["verification_log"]
    payload = dict(entry)
    if payload.get("stream_id") is not None:
        payload["stream_id"] = normalize_stream_id(str(payload["stream_id"]))
    serialized = json.dumps(payload, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(serialized + "\n")
    return path


def backup_state_file(base_dir: Path, target: Path) -> str | None:
    if not target.exists():
        return None
    backup_dir = ensure_state_files(base_dir)["backups_dir"]
    stem = target.stem.replace(".", "-")
    suffix = target.suffix or ".bak"
    backup_name = f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}.bak"
    backup_path = backup_dir / backup_name
    shutil.copy2(target, backup_path)
    return str(backup_path)


def inspect_workflow_state(base_dir: Path) -> dict[str, Any]:
    policy_info = load_policy(base_dir)
    memory = get_memory_status(base_dir)
    task_state = get_task_state(base_dir, policy_info["data"])
    verification = get_verification_state(base_dir, task_state)
    paths = get_state_paths(base_dir)
    return {
        "workspace_root": str(find_workspace_root(base_dir)),
        "state_dir": str(paths["state_dir"]),
        "memory": memory,
        "policy": policy_info,
        "task_loop": task_state,
        "task_state": task_state,
        "verification": verification,
    }
