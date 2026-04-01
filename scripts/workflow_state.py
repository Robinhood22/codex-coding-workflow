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
        "readme": state_dir / "README.md",
        "memory": state_dir / "memory.md",
        "task_loop": state_dir / "active-task-loop.md",
        "verification_log": state_dir / "verification-log.jsonl",
        "policy": state_dir / "policy.json",
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
    return f"# Active Task Loop\nUpdated: {now_timestamp()}\n\nNo active task loop yet.\n"


def default_readme_text() -> str:
    return (
        "# Codex Workflows State\n\n"
        "This directory stores repo-local workflow state for the "
        "`codex-coding-workflows` plugin.\n\n"
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
    task_loop = get_task_loop_status(base_dir, policy_info["data"])
    verification = get_verification_state(base_dir, task_loop)
    paths = get_state_paths(base_dir)
    return {
        "workspace_root": str(find_workspace_root(base_dir)),
        "state_dir": str(paths["state_dir"]),
        "memory": memory,
        "policy": policy_info,
        "task_loop": task_loop,
        "verification": verification,
    }
