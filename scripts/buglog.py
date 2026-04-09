#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workflow_state import (
    append_buglog_entry,
    get_buglog_state,
    search_buglog_entries,
)


def load_json_argument(inline_json: str | None, file_path: str | None) -> dict[str, Any]:
    if inline_json:
        raw = inline_json
    elif file_path:
        raw = Path(file_path).expanduser().read_text(encoding="utf-8")
    else:
        raise SystemExit("Provide --append-json or --append-file.")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid buglog JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("Buglog payload must decode to a JSON object.")
    return parsed


def render_search_text(state: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    lines = [
        f"Bug log: {state['status']}",
        f"Entries: {state['entry_count']}",
        f"Matches: {len(matches)}",
    ]
    if state["invalid_lines"] > 0:
        lines.append(f"Invalid lines: {state['invalid_lines']}")

    for item in matches:
        entry = item["entry"]
        tags = ", ".join(entry.get("tags", [])) or "none"
        lines.extend(
            [
                "",
                f"- {entry['file']} [{entry['timestamp']}] score={item['score']}",
                f"  Symptom: {entry['symptom']}",
                f"  Root cause: {entry['root_cause']}",
                f"  Fix: {entry['fix']}",
                f"  Tags: {tags}",
                f"  Source: {entry['source']}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search and append repo-local bug memory.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--search", type=str, default=None, help="Search term for bug memory.")
    parser.add_argument("--path", type=str, default=None, help="Restrict search to one workspace-relative path or subtree.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of search results to return.")
    parser.add_argument("--append-json", type=str, default=None, help="Append one buglog entry encoded as a JSON object.")
    parser.add_argument("--append-file", type=str, default=None, help="Append one buglog entry from a JSON file.")
    args = parser.parse_args()

    base_dir = Path(args.repo).expanduser()

    if args.append_json or args.append_file:
        payload = load_json_argument(args.append_json, args.append_file)
        path = append_buglog_entry(base_dir, payload)
        result = {
            "action": "append",
            "path": str(path),
            "state": get_buglog_state(base_dir),
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Appended buglog entry to {path}")
            print(f"Bug log: {result['state']['status']} ({result['state']['entry_count']} entries)")
        return 0

    if args.search:
        state = get_buglog_state(base_dir)
        matches = search_buglog_entries(
            base_dir,
            args.search,
            path=args.path,
            limit=args.limit,
        )
        result = {
            "action": "search",
            "query": args.search,
            "path_filter": args.path,
            "state": state,
            "matches": matches,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(render_search_text(state, matches))
        return 0

    raise SystemExit("No operation requested. Use --search, --append-json, or --append-file.")


if __name__ == "__main__":
    raise SystemExit(main())
