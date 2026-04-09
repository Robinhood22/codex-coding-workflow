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
SCHEMA_VERSION = 3
REQUIRED_MEMORY_SECTIONS = (
    "Stable Facts",
    "Preferences",
    "Constraints",
    "Open Questions",
)
OPTIONAL_MEMORY_SECTIONS = (
    "Do-Not-Repeat",
    "Decision Log",
)
MEMORY_SECTIONS = REQUIRED_MEMORY_SECTIONS + OPTIONAL_MEMORY_SECTIONS
MEMORY_SCOPES = {"local", "shared"}
SHARED_MEMORY_PREFIX = "[shared]"
VERIFICATION_REQUIRED_KEYS = ("timestamp", "scope", "checks", "verdict")
VALID_VERDICTS = {"PASS", "FAIL", "PARTIAL"}
MEMORY_CANDIDATE_REQUIRED_KEYS = ("scope", "section", "text", "source")
BUGLOG_REQUIRED_KEYS = ("timestamp", "file", "symptom", "root_cause", "fix", "tags", "source")

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("anthropic-api-key", r"\bsk-ant-(?:api|admin)[a-zA-Z0-9_\-]{20,}\b"),
    ("openai-api-key", r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}\b"),
    ("github-pat", r"\bgh[pousr]_[0-9A-Za-z]{20,}\b"),
    ("github-fine-grained-pat", r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ("aws-access-key", r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"),
    ("slack-token", r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    ("stripe-secret", r"\b(?:sk|rk)_(?:live|test|prod)_[A-Za-z0-9]{16,}\b"),
    ("npm-token", r"\bnpm_[A-Za-z0-9]{20,}\b"),
)

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
        "auto_refresh_shared_memory": True,
        "mirror_shared_into_local": True,
        "shared_secret_scan": True,
        "max_candidate_promotions": 50,
    },
}

ACTIVE_LINE_RE = re.compile(r"^- \[[ xX]\]\s+Active:", re.IGNORECASE)
CHECKBOX_LINE_RE = re.compile(r"^- \[[ xX]\]\s+")
SECTION_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")


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
    return {
        "state_dir": state_dir,
        "backups_dir": state_dir / BACKUPS_DIRNAME,
        "state_gitignore": state_dir / ".gitignore",
        "readme": state_dir / "README.md",
        "memory": state_dir / "memory.md",
        "shared_memory": state_dir / "shared-memory.md",
        "memory_candidates": state_dir / "memory-candidates.jsonl",
        "memory_sync_log": state_dir / "memory-sync-log.jsonl",
        "task_loop": state_dir / "active-task-loop.md",
        "verification_log": state_dir / "verification-log.jsonl",
        "buglog": state_dir / "buglog.jsonl",
        "policy": state_dir / "policy.json",
        "runtime_dir": state_dir / "runtime",
        "hook_state": state_dir / "runtime" / "hook-state.json",
    }


def default_memory_sections() -> dict[str, list[str]]:
    return {
        "Stable Facts": ["- No durable project facts recorded yet."],
        "Preferences": ["- No explicit workflow preferences recorded yet."],
        "Constraints": ["- No durable constraints recorded yet."],
        "Open Questions": ["- None."],
        "Do-Not-Repeat": ["- No repeated pitfalls recorded yet."],
        "Decision Log": ["- No durable decisions recorded yet."],
    }


def get_default_section_lines(section_name: str) -> list[str]:
    return [line.strip() for line in default_memory_sections().get(section_name, [])]


def render_memory_document(title: str, section_map: dict[str, list[str]]) -> str:
    lines = [title, ""]
    defaults = default_memory_sections()
    for section in MEMORY_SECTIONS:
        lines.append(f"## {section}")
        body_lines = [line.rstrip() for line in section_map.get(section, []) if line.strip()]
        if not body_lines:
            body_lines = defaults[section]
        lines.extend(body_lines)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_memory_text(section_map: dict[str, list[str]]) -> str:
    return render_memory_document("# Project Memory", section_map)


def default_memory_text() -> str:
    return render_memory_text(default_memory_sections())


def default_shared_memory_text() -> str:
    return render_memory_document("# Shared Memory", default_memory_sections())


def default_task_loop_text() -> str:
    return f"# Active Task Loop\nUpdated: {now_timestamp()}\n\nNo active task loop yet.\n"


def default_readme_text() -> str:
    return (
        "# Codex Workflows State\n\n"
        "This directory stores repo-local workflow state for the "
        "`codex-coding-workflows` plugin.\n\n"
        "Local memory lives in `memory.md`, shared memory lives in `shared-memory.md`, "
        "pending automatic memory promotions live in `memory-candidates.jsonl`, sync "
        "history lives in `memory-sync-log.jsonl`, and confirmed bug-fix recall lives "
        "in `buglog.jsonl`.\n\n"
        "The `backups/` directory stores repair-time snapshots before malformed "
        "workflow files are rewritten. Runtime-only hook data lives under `runtime/` "
        "and is ignored via `.codex-workflows/.gitignore`.\n"
    )


def serialize_policy(policy: dict[str, Any]) -> str:
    return json.dumps(deep_merge(DEFAULT_POLICY, policy), indent=2) + "\n"


def ensure_state_files(base_dir: Path) -> dict[str, Path]:
    paths = get_state_paths(base_dir)
    state_dir = paths["state_dir"]
    state_dir.mkdir(parents=True, exist_ok=True)
    paths["backups_dir"].mkdir(parents=True, exist_ok=True)

    defaults = {
        "state_gitignore": "runtime/\n",
        "readme": default_readme_text(),
        "memory": default_memory_text(),
        "shared_memory": default_shared_memory_text(),
        "memory_candidates": "",
        "memory_sync_log": "",
        "task_loop": default_task_loop_text(),
        "verification_log": "",
        "buglog": "",
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


def normalize_memory_document_text(raw_text: str, title: str) -> str:
    if not raw_text.strip():
        return render_memory_document(title, default_memory_sections())

    parsed = parse_memory_sections(raw_text)
    defaults = default_memory_sections()
    cleaned: dict[str, list[str]] = {}

    for section in MEMORY_SECTIONS:
        lines = [line for line in parsed.get(section, []) if line.strip()]
        cleaned[section] = lines or defaults[section]

    return render_memory_document(title, cleaned)


def get_memory_file_status(path: Path, display_name: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "status": "missing",
            "missing_sections": list(REQUIRED_MEMORY_SECTIONS),
            "missing_optional_sections": list(OPTIONAL_MEMORY_SECTIONS),
            "reasons": [f"{display_name} is missing."],
        }

    parsed = parse_memory_sections(path.read_text(encoding="utf-8"))
    missing_required_sections = [
        section
        for section in REQUIRED_MEMORY_SECTIONS
        if not any(line.strip() for line in parsed.get(section, []))
    ]
    missing_optional_sections = [
        section
        for section in OPTIONAL_MEMORY_SECTIONS
        if not any(line.strip() for line in parsed.get(section, []))
    ]
    return {
        "path": str(path),
        "exists": True,
        "status": "healthy" if not missing_required_sections else "invalid",
        "missing_sections": missing_required_sections,
        "missing_optional_sections": missing_optional_sections,
        "reasons": [] if not missing_required_sections else [
            f"{display_name} is missing required sections or section content."
        ],
    }


def get_memory_status(base_dir: Path) -> dict[str, Any]:
    return get_memory_file_status(get_state_paths(base_dir)["memory"], "memory.md")


def get_shared_memory_status(base_dir: Path) -> dict[str, Any]:
    return get_memory_file_status(
        get_state_paths(base_dir)["shared_memory"],
        "shared-memory.md",
    )


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


def normalize_task_loop_text(raw_text: str) -> str:
    task_lines: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("# ") or line.startswith("Updated:"):
            continue
        task_lines.append(normalize_task_item(line))

    if not task_lines:
        body = "No active task loop yet."
    else:
        active_indices = [index for index, line in enumerate(task_lines) if ACTIVE_LINE_RE.match(line)]
        if not active_indices:
            task_lines[0] = promote_to_active(task_lines[0])
        else:
            first_active = active_indices[0]
            task_lines[first_active] = promote_to_active(task_lines[first_active])
            for index in active_indices[1:]:
                task_lines[index] = demote_to_pending(task_lines[index])
        body = "\n".join(task_lines)

    return f"# Active Task Loop\nUpdated: {now_timestamp()}\n\n{body}\n"


def get_task_loop_status(base_dir: Path, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    path = get_state_paths(base_dir)["task_loop"]
    policy_data = policy or load_policy(base_dir)["data"]
    stale_after = int(policy_data["task_loop"]["stale_after_minutes"])

    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "status": "missing",
            "updated_at": None,
            "active_step_count": 0,
            "stale": False,
            "placeholder": True,
            "reasons": ["active-task-loop.md is missing."],
        }

    text = path.read_text(encoding="utf-8")
    updated_at = extract_updated_at(text)
    updated_dt = parse_timestamp(updated_at)
    active_step_count = sum(
        1 for line in text.splitlines() if ACTIVE_LINE_RE.match(line.strip())
    )
    placeholder = "No active task loop yet." in text or not text.strip()
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

    return {
        "path": str(path),
        "exists": True,
        "status": status,
        "updated_at": updated_at,
        "active_step_count": active_step_count,
        "stale": stale,
        "placeholder": placeholder,
        "reasons": reasons,
    }


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
    return reasons


def get_verification_state(
    base_dir: Path,
    task_loop_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded = load_verification_entries(base_dir)
    entries = loaded["entries"]
    latest_entry = entries[-1] if entries else None
    latest_at = parse_timestamp((latest_entry or {}).get("timestamp"))
    task_status = task_loop_status or get_task_loop_status(base_dir)
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


def normalize_workspace_relative_path(base_dir: Path, raw_path: Any) -> str | None:
    text = str(raw_path or "").strip()
    if not text:
        return None

    candidate = Path(text).expanduser()
    workspace_root = find_workspace_root(base_dir)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(workspace_root)
        except ValueError:
            return None
    else:
        relative = candidate

    parts: list[str] = []
    for part in relative.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        parts.append(part)

    if not parts:
        return None
    return Path(*parts).as_posix()


def normalize_buglog_tags(raw_tags: Any) -> tuple[list[str] | None, list[str]]:
    if not isinstance(raw_tags, list):
        return None, ["tags must be a JSON array."]

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_tags:
        if not isinstance(item, str):
            return None, ["tags entries must be strings."]
        tag = item.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized, []


def sanitize_buglog_entry(
    base_dir: Path,
    entry: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(entry)
    reasons: list[str] = []

    for key in BUGLOG_REQUIRED_KEYS:
        if key not in entry:
            reasons.append(f"Missing required key: {key}")

    timestamp = str(entry.get("timestamp", "")).strip()
    if not timestamp:
        reasons.append("timestamp must be non-empty.")
    elif parse_timestamp(timestamp) is None:
        reasons.append("timestamp is not a valid ISO 8601 value.")
    else:
        normalized["timestamp"] = timestamp

    file_path = normalize_workspace_relative_path(base_dir, entry.get("file"))
    if file_path is None:
        reasons.append(
            "file must be a workspace-relative path or an absolute path inside the workspace."
        )
    else:
        normalized["file"] = file_path

    for key in ("symptom", "root_cause", "fix", "source"):
        value = str(entry.get(key, "")).strip()
        if not value:
            reasons.append(f"{key} must be non-empty.")
        else:
            normalized[key] = value

    tags, tag_reasons = normalize_buglog_tags(entry.get("tags"))
    reasons.extend(tag_reasons)
    if tags is not None:
        normalized["tags"] = tags

    return normalized, reasons


def validate_buglog_entry(base_dir: Path, entry: dict[str, Any]) -> list[str]:
    _, reasons = sanitize_buglog_entry(base_dir, entry)
    return reasons


def load_buglog_entries(base_dir: Path) -> dict[str, Any]:
    path = get_state_paths(base_dir)["buglog"]
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "entries": [],
            "invalid_lines": 0,
            "invalid_reasons": [],
            "line_records": [],
        }

    entries: list[dict[str, Any]] = []
    invalid_lines = 0
    invalid_reasons: list[str] = []
    line_records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: invalid JSON ({exc}).")
            line_records.append({"raw": line, "valid": False})
            continue
        if not isinstance(parsed, dict):
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: entry must decode to a JSON object.")
            line_records.append({"raw": line, "valid": False})
            continue
        normalized, reasons = sanitize_buglog_entry(base_dir, parsed)
        if reasons:
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: {'; '.join(reasons)}")
            line_records.append({"raw": line, "valid": False})
            continue
        entries.append(normalized)
        line_records.append({"raw": line, "valid": True, "entry": normalized})

    return {
        "path": str(path),
        "exists": True,
        "entries": entries,
        "invalid_lines": invalid_lines,
        "invalid_reasons": invalid_reasons,
        "line_records": line_records,
    }


def append_buglog_entry(base_dir: Path, entry: dict[str, Any]) -> Path:
    payload = dict(entry)
    payload.setdefault("timestamp", now_timestamp())
    normalized, reasons = sanitize_buglog_entry(base_dir, payload)
    if reasons:
        raise SystemExit("Invalid buglog entry:\n- " + "\n- ".join(reasons))

    path = ensure_state_files(base_dir)["buglog"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(normalized, sort_keys=True) + "\n")
    return path


def buglog_path_matches(base_dir: Path, path_filter: str | None, entry_path: str) -> bool:
    if not path_filter:
        return True
    normalized_filter = normalize_workspace_relative_path(base_dir, path_filter)
    if normalized_filter is None:
        normalized_filter = str(path_filter).strip().replace("\\", "/").strip("/")
    if not normalized_filter:
        return True
    return entry_path == normalized_filter or entry_path.startswith(f"{normalized_filter.rstrip('/')}/")


def score_buglog_entry(entry: dict[str, Any], term: str) -> int:
    query = term.strip().lower()
    if not query:
        return 0

    score = 0
    searchable_fields = {
        "file": 3,
        "symptom": 2,
        "root_cause": 2,
        "fix": 2,
        "tags": 1,
        "source": 1,
    }
    for field, weight in searchable_fields.items():
        value = entry.get(field)
        if isinstance(value, list):
            haystack = " ".join(str(item) for item in value)
        else:
            haystack = str(value or "")
        lowered = haystack.lower()
        if query in lowered:
            score += max(1, lowered.count(query)) * weight

    file_value = str(entry.get("file", ""))
    if file_value == term or file_value.lower().endswith(query):
        score += 2
    return score


def search_buglog_entries(
    base_dir: Path,
    term: str,
    *,
    path: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    loaded = load_buglog_entries(base_dir)
    ranked: list[dict[str, Any]] = []
    for entry in loaded["entries"]:
        if not buglog_path_matches(base_dir, path, str(entry.get("file", ""))):
            continue
        score = score_buglog_entry(entry, term)
        if score <= 0:
            continue
        ranked.append({"score": score, "entry": entry})

    ranked.sort(
        key=lambda item: (item["score"], str(item["entry"].get("timestamp", ""))),
        reverse=True,
    )
    return ranked[: max(0, limit)]


def get_buglog_state(base_dir: Path) -> dict[str, Any]:
    loaded = load_buglog_entries(base_dir)
    latest = loaded["entries"][-1] if loaded["entries"] else None
    if not loaded["exists"]:
        status = "missing"
    elif loaded["invalid_lines"] > 0:
        status = "invalid"
    else:
        status = "healthy"

    return {
        "path": loaded["path"],
        "exists": loaded["exists"],
        "status": status,
        "entry_count": len(loaded["entries"]),
        "invalid_lines": loaded["invalid_lines"],
        "invalid_reasons": loaded["invalid_reasons"],
        "latest_timestamp": (latest or {}).get("timestamp"),
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
    docs_only = categories.get("docs", 0) > 0 and code_like_changes == 0 and categories.get("other", 0) == 0
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


def update_task_loop(base_dir: Path, raw_text: str) -> Path:
    path = ensure_state_files(base_dir)["task_loop"]
    path.write_text(normalize_task_loop_text(raw_text), encoding="utf-8")
    return path


def normalize_section_name(section_name: str | None) -> str:
    if not section_name:
        return "Stable Facts"
    normalized = section_name.strip().lower()
    for section in MEMORY_SECTIONS:
        if section.lower() == normalized:
            return section
    raise SystemExit(
        f"Unknown memory section {section_name!r}. Expected one of: {', '.join(MEMORY_SECTIONS)}"
    )


def insert_bullet_in_section(
    markdown_text: str,
    section_name: str,
    bullet_text: str,
    title: str = "# Project Memory",
) -> str:
    normalized = normalize_memory_document_text(markdown_text, title)
    bullet = bullet_text.strip()
    if not bullet.startswith("- "):
        bullet = f"- {bullet}"
    if bullet in normalized:
        return normalized

    sections = parse_memory_sections(normalized)
    defaults = set(get_default_section_lines(section_name))
    existing_lines = [
        line.strip()
        for line in sections.get(section_name, [])
        if line.strip() and line.strip() not in defaults
    ]
    if bullet in existing_lines:
        return render_memory_document(title, sections)

    sections[section_name] = existing_lines + [bullet]
    return render_memory_document(title, sections)


def append_memory_fact(base_dir: Path, fact: str) -> Path:
    return append_memory_entry(base_dir, fact, section="Stable Facts", scope="local")


def scan_text_for_secrets(text: str) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for rule_id, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            matches.append({"rule_id": rule_id, "label": rule_id.replace("-", " ")})
    return matches


def append_memory_entry(
    base_dir: Path,
    text: str,
    section: str = "Stable Facts",
    scope: str = "local",
) -> Path:
    if scope not in MEMORY_SCOPES:
        raise SystemExit(f"Unknown memory scope {scope!r}.")
    normalized_section = normalize_section_name(section)
    paths = ensure_state_files(base_dir)
    path = paths["memory"] if scope == "local" else paths["shared_memory"]
    if scope == "shared":
        secret_matches = scan_text_for_secrets(text)
        if secret_matches:
            labels = ", ".join(match["label"] for match in secret_matches)
            raise SystemExit(
                f"Refusing to write shared memory because secret-like content was detected: {labels}"
            )

    current = path.read_text(encoding="utf-8")
    title = "# Project Memory" if scope == "local" else "# Shared Memory"
    updated = insert_bullet_in_section(current, normalized_section, text, title=title)
    path.write_text(updated, encoding="utf-8")
    return path


def append_shared_memory_fact(base_dir: Path, fact: str) -> Path:
    return append_memory_entry(base_dir, fact, section="Stable Facts", scope="shared")


def is_shared_mirror_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(f"- {SHARED_MEMORY_PREFIX} ")


def mirror_shared_line(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("- "):
        stripped = f"- {stripped}"
    body = stripped[2:].strip()
    if body.startswith(f"{SHARED_MEMORY_PREFIX} "):
        return stripped
    return f"- {SHARED_MEMORY_PREFIX} {body}"


def mirror_shared_memory_into_local(base_dir: Path) -> dict[str, Any]:
    paths = ensure_state_files(base_dir)
    local_sections = parse_memory_sections(paths["memory"].read_text(encoding="utf-8"))
    shared_sections = parse_memory_sections(paths["shared_memory"].read_text(encoding="utf-8"))

    merged_sections: dict[str, list[str]] = {}
    mirrored_count = 0
    for section in MEMORY_SECTIONS:
        base_lines = [
            line
            for line in local_sections.get(section, [])
            if line.strip() and not is_shared_mirror_line(line)
        ]
        default_lines = set(get_default_section_lines(section))
        shared_lines: list[str] = []
        for line in shared_sections.get(section, []):
            if not line.strip() or line.strip() in default_lines:
                continue
            mirrored_line = mirror_shared_line(line)
            if mirrored_line not in shared_lines:
                shared_lines.append(mirrored_line)
        mirrored_count += len(shared_lines)
        merged_sections[section] = base_lines + shared_lines

    paths["memory"].write_text(render_memory_text(merged_sections), encoding="utf-8")
    return {
        "path": str(paths["memory"]),
        "shared_source_path": str(paths["shared_memory"]),
        "mirrored_count": mirrored_count,
    }


def validate_memory_candidate(entry: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in MEMORY_CANDIDATE_REQUIRED_KEYS:
        if key not in entry:
            reasons.append(f"Missing required key: {key}")
    scope = str(entry.get("scope", ""))
    if scope and scope not in MEMORY_SCOPES:
        reasons.append(f"scope must be one of: {', '.join(sorted(MEMORY_SCOPES))}")
    if "section" in entry:
        try:
            normalize_section_name(str(entry["section"]))
        except SystemExit as exc:
            reasons.append(str(exc))
    if "text" in entry and not str(entry["text"]).strip():
        reasons.append("text must be non-empty.")
    if "source" in entry and not str(entry["source"]).strip():
        reasons.append("source must be non-empty.")
    timestamp = entry.get("timestamp")
    if timestamp is not None and parse_timestamp(str(timestamp)) is None:
        reasons.append("timestamp is not a valid ISO 8601 value.")
    return reasons


def load_memory_candidate_entries(base_dir: Path) -> dict[str, Any]:
    path = get_state_paths(base_dir)["memory_candidates"]
    if not path.exists():
        return {
            "path": str(path),
            "entries": [],
            "invalid_lines": 0,
            "invalid_reasons": [],
            "line_records": [],
        }

    entries: list[dict[str, Any]] = []
    invalid_lines = 0
    invalid_reasons: list[str] = []
    line_records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: invalid JSON ({exc}).")
            line_records.append({"raw": line, "valid": False})
            continue
        if not isinstance(parsed, dict):
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: entry must decode to a JSON object.")
            line_records.append({"raw": line, "valid": False})
            continue
        reasons = validate_memory_candidate(parsed)
        if reasons:
            invalid_lines += 1
            invalid_reasons.append(f"Line {line_number}: {'; '.join(reasons)}")
            line_records.append({"raw": line, "valid": False})
            continue
        candidate = dict(parsed)
        candidate.setdefault("timestamp", now_timestamp())
        entries.append(candidate)
        line_records.append({"raw": line, "valid": True, "entry": candidate})

    return {
        "path": str(path),
        "entries": entries,
        "invalid_lines": invalid_lines,
        "invalid_reasons": invalid_reasons,
        "line_records": line_records,
    }


def append_memory_candidate(base_dir: Path, entry: dict[str, Any]) -> Path:
    candidate = dict(entry)
    candidate.setdefault("timestamp", now_timestamp())
    reasons = validate_memory_candidate(candidate)
    if reasons:
        raise SystemExit("Invalid memory candidate:\n- " + "\n- ".join(reasons))
    if str(candidate.get("scope")) == "shared":
        secret_matches = scan_text_for_secrets(str(candidate.get("text", "")))
        if secret_matches:
            labels = ", ".join(match["label"] for match in secret_matches)
            raise SystemExit(
                "Refusing to queue shared memory candidate because secret-like content was "
                f"detected: {labels}"
            )
    path = ensure_state_files(base_dir)["memory_candidates"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(candidate, sort_keys=True) + "\n")
    return path


def load_memory_sync_entries(base_dir: Path) -> dict[str, Any]:
    path = get_state_paths(base_dir)["memory_sync_log"]
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
        entries.append(parsed)
    return {
        "path": str(path),
        "entries": entries,
        "invalid_lines": invalid_lines,
        "invalid_reasons": invalid_reasons,
    }


def append_memory_sync_entry(base_dir: Path, entry: dict[str, Any]) -> Path:
    payload = dict(entry)
    payload.setdefault("timestamp", now_timestamp())
    path = ensure_state_files(base_dir)["memory_sync_log"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return path


def promote_memory_candidates(base_dir: Path) -> dict[str, Any]:
    paths = ensure_state_files(base_dir)
    policy = load_policy(base_dir)["data"]
    loaded = load_memory_candidate_entries(base_dir)
    max_items = int(policy["memory"].get("max_candidate_promotions", 50))
    shared_secret_scan = bool(policy["memory"].get("shared_secret_scan", True))

    promoted_local = 0
    promoted_shared = 0
    blocked_shared: list[dict[str, Any]] = []
    processed = 0
    retained_lines: list[str] = []

    for record in loaded["line_records"]:
        if not record.get("valid"):
            retained_lines.append(str(record["raw"]))
            continue
        entry = dict(record["entry"])
        if processed >= max_items:
            retained_lines.append(str(record["raw"]))
            continue

        scope = str(entry["scope"])
        section = normalize_section_name(str(entry["section"]))
        text = str(entry["text"]).strip()
        if scope == "shared" and shared_secret_scan:
            secret_matches = scan_text_for_secrets(text)
            if secret_matches:
                blocked_shared.append(
                    {
                        "section": section,
                        "source": str(entry["source"]),
                        "text_redacted": True,
                        "secret_matches": secret_matches,
                    }
                )
                processed += 1
                continue

        append_memory_entry(base_dir, text, section=section, scope=scope)
        if scope == "local":
            promoted_local += 1
        else:
            promoted_shared += 1
        processed += 1

    paths["memory_candidates"].write_text(
        ("\n".join(retained_lines) + "\n") if retained_lines else "",
        encoding="utf-8",
    )

    mirrored = {"mirrored_count": 0, "path": str(paths["memory"])}
    if policy["memory"].get("auto_refresh_shared_memory", False) and policy["memory"].get(
        "mirror_shared_into_local", False
    ):
        mirrored = mirror_shared_memory_into_local(base_dir)

    summary = {
        "promoted_local": promoted_local,
        "promoted_shared": promoted_shared,
        "blocked_shared": blocked_shared,
        "retained_candidates": len(
            [line for line in retained_lines if line.strip().startswith("{")]
        ),
        "invalid_candidate_lines": loaded["invalid_lines"],
        "mirrored_shared_count": mirrored["mirrored_count"],
    }
    append_memory_sync_entry(
        base_dir,
        {
            "action": "auto_refresh",
            "summary": summary,
        },
    )
    return summary


def get_memory_candidate_state(base_dir: Path) -> dict[str, Any]:
    loaded = load_memory_candidate_entries(base_dir)
    if loaded["invalid_lines"] > 0:
        status = "invalid"
    elif loaded["entries"]:
        status = "pending"
    else:
        status = "healthy"
    return {
        "path": loaded["path"],
        "pending_count": len(loaded["entries"]),
        "invalid_lines": loaded["invalid_lines"],
        "invalid_reasons": loaded["invalid_reasons"],
        "status": status,
    }


def get_memory_sync_state(base_dir: Path) -> dict[str, Any]:
    loaded = load_memory_sync_entries(base_dir)
    latest = loaded["entries"][-1] if loaded["entries"] else None
    return {
        "path": loaded["path"],
        "entry_count": len(loaded["entries"]),
        "invalid_lines": loaded["invalid_lines"],
        "invalid_reasons": loaded["invalid_reasons"],
        "latest_timestamp": (latest or {}).get("timestamp"),
        "latest_action": (latest or {}).get("action"),
        "status": "invalid" if loaded["invalid_lines"] > 0 else "healthy",
    }


def append_verification_entry(base_dir: Path, entry: dict[str, Any]) -> Path:
    path = ensure_state_files(base_dir)["verification_log"]
    serialized = json.dumps(entry, sort_keys=True)
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
    shared_memory = get_shared_memory_status(base_dir)
    memory_candidates = get_memory_candidate_state(base_dir)
    memory_sync = get_memory_sync_state(base_dir)
    task_loop = get_task_loop_status(base_dir, policy_info["data"])
    verification = get_verification_state(base_dir, task_loop)
    buglog = get_buglog_state(base_dir)
    paths = get_state_paths(base_dir)
    return {
        "workspace_root": str(find_workspace_root(base_dir)),
        "state_dir": str(paths["state_dir"]),
        "memory": memory,
        "shared_memory": shared_memory,
        "memory_candidates": memory_candidates,
        "memory_sync": memory_sync,
        "buglog": buglog,
        "policy": policy_info,
        "task_loop": task_loop,
        "verification": verification,
    }
