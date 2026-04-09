#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

from workflow_state import find_git_root, find_workspace_root, get_state_dir, now_timestamp


MAX_FILE_SIZE_BYTES = 1_000_000
MAP_FILENAME = "project-map.md"
TIMESTAMP_PREFIX = "Generated:"
SECTION_ORDER = (
    "Manifests And Config",
    "Entrypoints And Router Roots",
    "Server, CLI, And Scripts",
    "Tests And Test Config",
    "Changed Files",
)
SECTION_EMPTY_MESSAGES = {
    "Manifests And Config": "- No focused manifest or config files detected.",
    "Entrypoints And Router Roots": "- No obvious app entrypoints or router roots detected.",
    "Server, CLI, And Scripts": "- No focused server, CLI, or script entrypoints detected.",
    "Tests And Test Config": "- No focused test files or runner configs detected.",
    "Changed Files": "- No changed files detected.",
}
EXCLUDED_DIR_NAMES = {
    ".codex-workflows",
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".parcel-cache",
    ".turbo",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "tmp",
}
CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".mts",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}
TEXT_EXTENSIONS = CODE_EXTENSIONS | {
    ".cfg",
    ".conf",
    ".css",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".mdx",
    ".rst",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
MANIFEST_FILENAMES = {
    "Cargo.toml",
    "Gemfile",
    "Makefile",
    "Pipfile",
    "composer.json",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}
MANIFEST_CONFIG_PATTERNS = (
    "*.config.js",
    "*.config.cjs",
    "*.config.mjs",
    "*.config.ts",
    "*.config.cts",
    "*.config.mts",
    ".eslintrc",
    ".eslintrc.*",
    ".prettierrc",
    ".prettierrc.*",
    "babel.config.*",
    "eslint.config.*",
    "next.config.*",
    "rollup.config.*",
    "tailwind.config.*",
    "tsconfig*.json",
    "vite.config.*",
    "webpack.config.*",
)
TEST_CONFIG_PATTERNS = (
    "conftest.py",
    "cypress.config.*",
    "jest.config.*",
    "noxfile.py",
    "playwright.config.*",
    "pytest.ini",
    "tox.ini",
    "vitest.config.*",
)
ENTRYPOINT_STEMS = {"app", "bootstrap", "index", "main", "wsgi", "asgi"}
ENTRYPOINT_DIRS = {"app", "apps", "client", "frontend", "pages", "src", "web"}
ROUTER_TERMS = ("router", "route", "routes")
SERVER_CLI_STEMS = {"cli", "console", "dev", "manage", "run", "serve", "server", "start"}
SERVER_CLI_DIRS = {"bin", "cli", "cmd", "scripts", "server", "servers"}
TEST_DIRS = {"__tests__", "spec", "specs", "test", "tests"}
TEST_SUFFIX_PATTERNS = (
    ".spec.js",
    ".spec.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".test.js",
    ".test.jsx",
    ".test.ts",
    ".test.tsx",
    "_test.py",
)
BINARY_EXTENSIONS = {
    ".7z",
    ".a",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".dmg",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lock",
    ".mp3",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".so",
    ".tar",
    ".ttf",
    ".wav",
    ".webm",
    ".woff",
    ".woff2",
    ".zip",
}


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def get_project_map_path(base_dir: Path) -> Path:
    state_dir = get_state_dir(base_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_gitignore = state_dir / ".gitignore"
    if not state_gitignore.exists():
        state_gitignore.write_text("runtime/\n", encoding="utf-8")
    return state_dir / MAP_FILENAME


def is_excluded_path(path: Path, workspace_root: Path) -> bool:
    try:
        relative = path.relative_to(workspace_root)
    except ValueError:
        return True

    if not relative.parts:
        return True
    if relative.name.startswith(".env"):
        return True
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
        return True
    return False


def should_skip_by_size(path: Path) -> bool:
    try:
        return path.stat().st_size > MAX_FILE_SIZE_BYTES
    except OSError:
        return True


def is_probably_text_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return False
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def read_text_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def relative_path(path: Path, workspace_root: Path) -> str:
    return path.relative_to(workspace_root).as_posix()


def parse_status_paths(status_output: str) -> list[str]:
    paths: set[str] = set()
    for line in status_output.splitlines():
        if not line.strip():
            continue
        entry = line[3:] if len(line) > 3 else line
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        cleaned = entry.strip()
        if cleaned:
            paths.add(cleaned.replace("\\", "/"))
    return sorted(paths)


def get_changed_paths(workspace_root: Path) -> set[str]:
    repo_root = find_git_root(workspace_root)
    if repo_root is None:
        return set()
    status = run_git(["status", "--short"], repo_root)
    if status.returncode != 0:
        return set()

    changed: set[str] = set()
    for raw in parse_status_paths(status.stdout):
        candidate = repo_root / Path(raw)
        if is_excluded_path(candidate, workspace_root):
            continue
        if not candidate.exists():
            continue
        if candidate.is_dir() or should_skip_by_size(candidate) or not is_probably_text_file(candidate):
            continue
        try:
            relative = candidate.relative_to(workspace_root).as_posix()
        except ValueError:
            continue
        changed.add(relative)
    return changed


def matches_any_pattern(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)


def is_test_file(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if lowered_parts & TEST_DIRS:
        return path.suffix.lower() in CODE_EXTENSIONS | {".py"}
    if matches_any_pattern(name, TEST_CONFIG_PATTERNS):
        return True
    if name.startswith("test_"):
        return True
    return any(name.endswith(pattern) for pattern in TEST_SUFFIX_PATTERNS)


def is_manifest_or_config(path: Path) -> bool:
    name = path.name
    return name in MANIFEST_FILENAMES or matches_any_pattern(name, MANIFEST_CONFIG_PATTERNS)


def is_entrypoint_or_router(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in CODE_EXTENSIONS:
        return False
    stem = path.stem.lower()
    lowered_parts = [part.lower() for part in path.parts]
    if any(term in stem for term in ROUTER_TERMS):
        return True
    if any(part in {"router", "routes"} for part in lowered_parts):
        return True
    return stem in ENTRYPOINT_STEMS and any(part in ENTRYPOINT_DIRS for part in lowered_parts)


def is_server_cli_or_script(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in CODE_EXTENSIONS:
        return False
    lowered_parts = [part.lower() for part in path.parts]
    stem = path.stem.lower()
    if any(part in SERVER_CLI_DIRS for part in lowered_parts[:-1]):
        return True
    if any(part in {"bin", "cli"} for part in lowered_parts):
        return True
    return stem in SERVER_CLI_STEMS


def choose_primary_section(path: Path) -> str | None:
    if is_test_file(path):
        return "Tests And Test Config"
    if is_manifest_or_config(path):
        return "Manifests And Config"
    if is_entrypoint_or_router(path):
        return "Entrypoints And Router Roots"
    if is_server_cli_or_script(path):
        return "Server, CLI, And Scripts"
    return None


def iter_relevant_files(workspace_root: Path, changed_paths: set[str]) -> list[Path]:
    selected: dict[str, Path] = {}
    for current_root, dirnames, filenames in os.walk(workspace_root, topdown=True):
        current_path = Path(current_root)
        dirnames[:] = [
            dirname
            for dirname in sorted(dirnames)
            if dirname not in EXCLUDED_DIR_NAMES
            and not is_excluded_path(current_path / dirname, workspace_root)
        ]
        for filename in sorted(filenames):
            candidate = current_path / filename
            if is_excluded_path(candidate, workspace_root):
                continue
            if should_skip_by_size(candidate) or not is_probably_text_file(candidate):
                continue
            relative = relative_path(candidate, workspace_root)
            if relative in changed_paths or choose_primary_section(candidate) is not None:
                selected[relative] = candidate
    return [selected[key] for key in sorted(selected)]


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def humanize_stem(path: Path) -> str:
    stem = path.stem
    if stem.lower() in {"index", "main", "app"} and len(path.parts) > 1:
        stem = f"{path.parts[-2]} {stem}"
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem)
    text = re.sub(r"[_\-.]+", " ", text)
    return collapse_whitespace(text).capitalize()


def extract_header_comment(path: Path, text: str) -> str | None:
    lines = text.splitlines()
    quote_delim: str | None = None
    quote_lines: list[str] = []
    in_block_comment = False
    block_lines: list[str] = []

    for raw in lines[:25]:
        line = raw.strip()
        if not line:
            if quote_lines or block_lines:
                break
            continue
        if line.startswith("#!"):
            continue
        if quote_delim is None and line.startswith(('"""', "'''")):
            delim = line[:3]
            tail = line[3:]
            if tail.endswith(delim) and len(tail) > 3:
                return collapse_whitespace(tail[:-3])
            quote_delim = delim
            if tail:
                quote_lines.append(tail)
            continue
        if quote_delim is not None:
            if line.endswith(quote_delim):
                content = line[:-3]
                if content:
                    quote_lines.append(content)
                return collapse_whitespace(" ".join(quote_lines))
            quote_lines.append(line)
            continue

        if line.startswith("/*"):
            in_block_comment = True
            block_line = line[2:]
            if "*/" in block_line:
                content = block_line.split("*/", 1)[0]
                return collapse_whitespace(content.lstrip("*").strip())
            if block_line:
                block_lines.append(block_line.lstrip("*").strip())
            continue
        if in_block_comment:
            if "*/" in line:
                block_lines.append(line.split("*/", 1)[0].lstrip("*").strip())
                return collapse_whitespace(" ".join(item for item in block_lines if item))
            block_lines.append(line.lstrip("*").strip())
            continue

        if path.suffix.lower() in {".md", ".mdx", ".rst"} and line.startswith("#"):
            return collapse_whitespace(line.lstrip("#").strip())
        if line.startswith("//"):
            return collapse_whitespace(line[2:])
        if line.startswith("#"):
            return collapse_whitespace(line[1:])
        break

    return None


def describe_manifest(path: Path, text: str) -> str | None:
    if path.name == "package.json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        name = str(parsed.get("name") or path.parent.name or "package").strip()
        scripts = parsed.get("scripts")
        if isinstance(scripts, dict) and scripts:
            script_names = ", ".join(sorted(str(key) for key in scripts.keys())[:4])
            return f"Package manifest for {name}; scripts: {script_names}"
        return f"Package manifest for {name}"

    if path.name in {"pyproject.toml", "Cargo.toml"} and tomllib is not None:
        try:
            parsed = tomllib.loads(text)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            if path.name == "pyproject.toml":
                project = parsed.get("project", {})
                if isinstance(project, dict) and project.get("name"):
                    return f"Python project manifest for {project['name']}"
            if path.name == "Cargo.toml":
                package = parsed.get("package", {})
                if isinstance(package, dict) and package.get("name"):
                    return f"Rust crate manifest for {package['name']}"

    if path.name == "go.mod":
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("module "):
                return f"Go module manifest for {stripped.split(' ', 1)[1].strip()}"
        return "Go module manifest"

    if path.name == "requirements.txt":
        return "Python dependency manifest"
    if path.name == "setup.cfg":
        return "Python setup configuration"
    if path.name == "setup.py":
        return "Python setup script"
    if path.name == "Makefile":
        return "Build task manifest"
    return None


def infer_description(path: Path, text: str) -> str:
    manifest = describe_manifest(path, text)
    if manifest:
        return manifest

    header = extract_header_comment(path, text)
    if header:
        return header

    name = path.name
    if is_test_file(path):
        return f"{humanize_stem(path)} test or runner config"
    if is_entrypoint_or_router(path):
        if any(term in path.stem.lower() for term in ROUTER_TERMS):
            return f"{humanize_stem(path)} router root"
        return f"{humanize_stem(path)} app entrypoint"
    if is_server_cli_or_script(path):
        return f"{humanize_stem(path)} script or entrypoint"
    if is_manifest_or_config(path):
        return f"{humanize_stem(path)} manifest or config"
    return f"{name} reference file"


def count_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def line_count_hint(count: int) -> str:
    return f"{count} line" if count == 1 else f"{count} lines"


def build_entry(path: Path, workspace_root: Path) -> dict[str, Any] | None:
    text = read_text_file(path)
    if text is None:
        return None

    return {
        "path": relative_path(path, workspace_root),
        "description": infer_description(path, text),
        "line_count": count_lines(text),
    }


def render_map(workspace_root: Path, sections: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        "# Project Map",
        "",
        f"{TIMESTAMP_PREFIX} {now_timestamp()}",
        f"Workspace: `{workspace_root}`",
        "",
    ]

    for section_name in SECTION_ORDER:
        lines.append(f"## {section_name}")
        entries = sections.get(section_name, [])
        if entries:
            for entry in entries:
                lines.append(
                    f"- `{entry['path']}` — {entry['description']}. "
                    f"{line_count_hint(entry['line_count'])}."
                )
        else:
            lines.append(SECTION_EMPTY_MESSAGES[section_name])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_project_map(base_dir: Path) -> tuple[str, dict[str, Any]]:
    workspace_root = find_workspace_root(base_dir)
    changed_paths = get_changed_paths(workspace_root)
    files = iter_relevant_files(workspace_root, changed_paths)
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTION_ORDER}

    for path in files:
        entry = build_entry(path, workspace_root)
        if entry is None:
            continue

        primary_section = choose_primary_section(path)
        if primary_section is not None:
            sections[primary_section].append(entry)
        if entry["path"] in changed_paths:
            sections["Changed Files"].append(entry)

    for section_name in SECTION_ORDER:
        sections[section_name] = sorted(
            sections[section_name],
            key=lambda item: item["path"],
        )

    metadata = {
        "workspace_root": str(workspace_root),
        "section_counts": {name: len(sections[name]) for name in SECTION_ORDER},
        "total_entries": sum(len(items) for items in sections.values()),
        "changed_file_count": len(changed_paths),
    }
    return render_map(workspace_root, sections), metadata


def normalize_for_compare(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith(TIMESTAMP_PREFIX):
            lines.append(f"{TIMESTAMP_PREFIX} <ignored>")
        else:
            lines.append(line.rstrip())
    return "\n".join(lines).rstrip() + "\n"


def generate_project_map(base_dir: Path) -> dict[str, Any]:
    output_path = get_project_map_path(base_dir)
    rendered, metadata = build_project_map(base_dir)
    output_path.write_text(rendered, encoding="utf-8")
    return {
        "action": "generate",
        "path": str(output_path),
        "workspace_root": metadata["workspace_root"],
        "section_counts": metadata["section_counts"],
        "total_entries": metadata["total_entries"],
        "changed_file_count": metadata["changed_file_count"],
    }


def check_project_map(base_dir: Path) -> dict[str, Any]:
    output_path = get_project_map_path(base_dir)
    rendered, metadata = build_project_map(base_dir)
    expected = normalize_for_compare(rendered)
    if not output_path.exists():
        return {
            "action": "check",
            "path": str(output_path),
            "workspace_root": metadata["workspace_root"],
            "status": "missing",
            "matches": False,
        }

    current = normalize_for_compare(output_path.read_text(encoding="utf-8"))
    matches = current == expected
    return {
        "action": "check",
        "path": str(output_path),
        "workspace_root": metadata["workspace_root"],
        "status": "match" if matches else "drift",
        "matches": matches,
    }


def render_text(result: dict[str, Any]) -> str:
    lines = [
        f"Action: {result['action']}",
        f"Path: {result['path']}",
    ]
    if result["action"] == "generate":
        lines.append(f"Total entries: {result['total_entries']}")
        lines.append(f"Changed files: {result['changed_file_count']}")
        lines.append("Section counts:")
        lines.extend(
            f"- {name}: {count}"
            for name, count in result["section_counts"].items()
        )
    else:
        lines.append(f"Status: {result['status']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or check a focused repo project map.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument("--generate", action="store_true", help="Generate .codex-workflows/project-map.md.")
    parser.add_argument("--check", action="store_true", help="Check whether .codex-workflows/project-map.md is current.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    if args.generate == args.check:
        raise SystemExit("Choose exactly one of --generate or --check.")

    base_dir = Path(args.repo).expanduser()
    result = generate_project_map(base_dir) if args.generate else check_project_map(base_dir)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result))
    return 0 if result.get("matches", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
