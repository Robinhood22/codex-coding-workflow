#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from workflow_state import find_workspace_root, is_workflow_state_path


EXECUTION_SCHEMA_VERSION = 1
DEFAULT_BRANCH_PREFIX = "codex/refactor"
VALID_CLEANUP_POLICIES = {
    "keep_on_change",
    "remove_if_clean",
    "keep_always",
    "remove_always",
}
VALID_CLEANUP_MODES = {"keep", "remove", "auto"}
VALID_DIRTY_REPO_POLICIES = {"reject", "allow"}


def now_timestamp() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def git_stdout(args: list[str], cwd: Path, description: str) -> str:
    result = run_git(args, cwd)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise SystemExit(f"Failed to {description}: {stderr}")
    return result.stdout.strip()


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    collapsed = re.sub(r"[^a-z0-9._-]+", "-", lowered)
    return collapsed.strip("-") or "run"


def ensure_git_repo(base_dir: Path) -> Path:
    result = run_git(["rev-parse", "--show-toplevel"], base_dir)
    if result.returncode != 0:
        raise SystemExit("Worktree isolation requires a git repository.")
    return Path(result.stdout.strip())


def execution_path_for_run(base_dir: Path, run_id: str) -> Path:
    workspace_root = find_workspace_root(base_dir)
    return workspace_root / ".codex-workflows" / "teams" / run_id / "execution.json"


def load_execution_metadata(base_dir: Path, run_id: str) -> dict[str, Any] | None:
    path = execution_path_for_run(base_dir, run_id)
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def write_execution_metadata(base_dir: Path, run_id: str, payload: dict[str, Any]) -> Path:
    path = execution_path_for_run(base_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    output = dict(payload)
    output.setdefault("schema_version", EXECUTION_SCHEMA_VERSION)
    output["updated_at"] = now_timestamp()
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return path


def branch_exists(repo_root: Path, branch_name: str) -> bool:
    result = run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"], repo_root)
    return result.returncode == 0


def default_worktree_root(repo_root: Path) -> Path:
    return repo_root.parent / ".codex-worktrees" / repo_root.name


def resolve_worktree_root(repo_root: Path, override: str | None) -> Path:
    if not override:
        return default_worktree_root(repo_root)
    candidate = Path(override).expanduser()
    if candidate.is_absolute():
        return candidate
    return (repo_root.parent / candidate).resolve()


def build_worktree_branch(run_id: str, branch_prefix: str) -> str:
    suffix = slugify(run_id)
    return f"{branch_prefix.rstrip('/')}-{suffix}"


def build_worktree_path(repo_root: Path, run_id: str, worktree_root: Path) -> Path:
    return worktree_root / slugify(run_id)


def repo_has_uncommitted_changes(repo_root: Path) -> bool:
    result = run_git(["status", "--short"], repo_root)
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        entry = line[3:] if len(line) > 3 else line
        path = entry.split(" -> ", 1)[-1].strip()
        if is_workflow_state_path(path):
            continue
        return True
    return False


def count_status_lines(repo_path: Path) -> int:
    result = run_git(["status", "--short"], repo_path)
    count = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        entry = line[3:] if len(line) > 3 else line
        path = entry.split(" -> ", 1)[-1].strip()
        if is_workflow_state_path(path):
            continue
        count += 1
    return count


def count_commits_ahead(repo_path: Path, base_branch: str | None) -> int:
    if not base_branch:
        return 0
    result = run_git(["rev-list", "--count", f"{base_branch}..HEAD"], repo_path)
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def resolve_context(
    base_dir: Path,
    run_id: str,
    base_branch: str | None,
    branch_prefix: str,
    worktree_root_override: str | None,
    cleanup_policy: str | None = None,
) -> dict[str, Any]:
    execution = load_execution_metadata(base_dir, run_id) or {}
    repo_root = ensure_git_repo(base_dir)
    resolved_base_branch = str(
        execution.get("base_branch")
        or base_branch
        or git_stdout(["branch", "--show-current"], repo_root, "detect the current branch")
        or "main"
    )
    resolved_branch = str(
        execution.get("worktree_branch") or build_worktree_branch(run_id, branch_prefix)
    )
    resolved_root = resolve_worktree_root(repo_root, worktree_root_override)
    resolved_path = Path(
        execution.get("worktree_path") or build_worktree_path(repo_root, run_id, resolved_root)
    )
    return {
        "run_id": run_id,
        "repo_root": repo_root,
        "base_branch": resolved_base_branch,
        "worktree_branch": resolved_branch,
        "worktree_root": resolved_root,
        "worktree_path": resolved_path,
        "cleanup_policy": str(execution.get("cleanup_policy") or cleanup_policy or "keep_on_change"),
        "dirty_repo_policy": str(execution.get("dirty_repo_policy") or "reject"),
        "execution": execution,
    }


def worktree_path_exists(path: Path) -> bool:
    return path.exists()


def looks_like_worktree(path: Path) -> bool:
    return (path / ".git").exists()


def inspect_worktree_for_run(
    base_dir: Path,
    run_id: str,
    base_branch: str | None = None,
    branch_prefix: str = DEFAULT_BRANCH_PREFIX,
    worktree_root_override: str | None = None,
) -> dict[str, Any]:
    context = resolve_context(
        base_dir=base_dir,
        run_id=run_id,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        worktree_root_override=worktree_root_override,
    )
    repo_root = context["repo_root"]
    worktree_path = context["worktree_path"]
    worktree_branch = context["worktree_branch"]
    path_exists = worktree_path_exists(worktree_path)
    branch_present = branch_exists(repo_root, worktree_branch)
    looks_valid = path_exists and looks_like_worktree(worktree_path)
    changed_files = count_status_lines(worktree_path) if looks_valid else 0
    commits_ahead = count_commits_ahead(worktree_path, context["base_branch"]) if looks_valid else 0
    has_changes = changed_files > 0 or commits_ahead > 0
    head_commit = (
        git_stdout(["rev-parse", "HEAD"], worktree_path, "read the worktree HEAD")
        if looks_valid
        else None
    )
    return {
        "action": "inspect",
        "status": "ok",
        "run_id": run_id,
        "repo_root": str(repo_root),
        "base_branch": context["base_branch"],
        "worktree_branch": worktree_branch,
        "worktree_path": str(worktree_path),
        "cleanup_policy": context["cleanup_policy"],
        "worktree_path_exists": path_exists,
        "worktree_branch_exists": branch_present,
        "looks_like_worktree": looks_valid,
        "changed_files": changed_files,
        "commits_ahead": commits_ahead,
        "has_changes": has_changes,
        "head_commit": head_commit,
    }


def create_worktree_for_run(
    base_dir: Path,
    run_id: str,
    base_branch: str,
    cleanup_policy: str = "keep_on_change",
    dirty_repo_policy: str = "reject",
    branch_prefix: str = DEFAULT_BRANCH_PREFIX,
    worktree_root_override: str | None = None,
) -> dict[str, Any]:
    if cleanup_policy not in VALID_CLEANUP_POLICIES:
        raise SystemExit(f"Invalid cleanup policy: {cleanup_policy!r}")
    if dirty_repo_policy not in VALID_DIRTY_REPO_POLICIES:
        raise SystemExit(f"Invalid dirty repo policy: {dirty_repo_policy!r}")

    context = resolve_context(
        base_dir=base_dir,
        run_id=run_id,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        worktree_root_override=worktree_root_override,
        cleanup_policy=cleanup_policy,
    )
    repo_root = context["repo_root"]
    worktree_path = context["worktree_path"]
    worktree_branch = context["worktree_branch"]

    if dirty_repo_policy == "reject" and repo_has_uncommitted_changes(repo_root):
        raise SystemExit(
            "Refusing to create a worktree because the main repository has uncommitted changes."
        )

    base_commit = git_stdout(
        ["rev-parse", context["base_branch"]],
        repo_root,
        f"resolve base branch {context['base_branch']!r}",
    )

    if worktree_path.exists():
        if looks_like_worktree(worktree_path) and branch_exists(repo_root, worktree_branch):
            result = inspect_worktree_for_run(
                base_dir=base_dir,
                run_id=run_id,
                base_branch=context["base_branch"],
                branch_prefix=branch_prefix,
                worktree_root_override=str(context["worktree_root"]),
            )
            result.update(
                {
                    "action": "create",
                    "base_commit": base_commit,
                    "cleanup_policy": cleanup_policy,
                    "dirty_repo_policy": dirty_repo_policy,
                    "resumed": True,
                }
            )
            return result
        raise SystemExit(
            f"Worktree path already exists and is not a recognized run worktree: {worktree_path}"
        )

    if branch_exists(repo_root, worktree_branch):
        raise SystemExit(
            f"Worktree branch {worktree_branch!r} already exists without the expected worktree path."
        )

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(
        ["worktree", "add", "-b", worktree_branch, str(worktree_path), context["base_branch"]],
        repo_root,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise SystemExit(f"Failed to create worktree: {stderr}")

    head_commit = git_stdout(["rev-parse", "HEAD"], worktree_path, "read the new worktree HEAD")
    return {
        "action": "create",
        "status": "ok",
        "run_id": run_id,
        "repo_root": str(repo_root),
        "base_branch": context["base_branch"],
        "base_commit": base_commit,
        "worktree_branch": worktree_branch,
        "worktree_path": str(worktree_path),
        "cleanup_policy": cleanup_policy,
        "dirty_repo_policy": dirty_repo_policy,
        "head_commit": head_commit,
        "resumed": False,
    }


def decide_cleanup(mode: str, cleanup_policy: str, has_changes: bool) -> tuple[str, str]:
    if mode == "keep":
        return "kept", "Cleanup mode explicitly requested to keep the worktree."
    if mode == "remove":
        return "removed", "Cleanup mode explicitly requested to remove the worktree."

    if cleanup_policy == "keep_always":
        return "kept", "Cleanup policy is keep_always."
    if cleanup_policy == "remove_always":
        return "removed", "Cleanup policy is remove_always."
    if has_changes:
        return "kept", f"Worktree has changes and cleanup_policy is {cleanup_policy}."
    return "removed", f"Worktree is clean and cleanup_policy is {cleanup_policy}."


def cleanup_worktree_for_run(
    base_dir: Path,
    run_id: str,
    mode: str,
    base_branch: str | None = None,
    branch_prefix: str = DEFAULT_BRANCH_PREFIX,
    worktree_root_override: str | None = None,
) -> dict[str, Any]:
    if mode not in VALID_CLEANUP_MODES:
        raise SystemExit(f"Invalid cleanup mode: {mode!r}")

    inspected = inspect_worktree_for_run(
        base_dir=base_dir,
        run_id=run_id,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        worktree_root_override=worktree_root_override,
    )
    if not inspected["worktree_path_exists"]:
        return {
            "action": "cleanup",
            "status": "ok",
            "run_id": run_id,
            "mode": mode,
            "decision": "skipped",
            "reason": "Worktree path does not exist.",
            "worktree_path": inspected["worktree_path"],
            "worktree_branch": inspected["worktree_branch"],
        }

    decision, reason = decide_cleanup(
        mode=mode,
        cleanup_policy=str(inspected["cleanup_policy"]),
        has_changes=bool(inspected["has_changes"]),
    )
    if decision == "kept":
        return {
            "action": "cleanup",
            "status": "ok",
            "run_id": run_id,
            "mode": mode,
            "decision": decision,
            "reason": reason,
            "worktree_path": inspected["worktree_path"],
            "worktree_branch": inspected["worktree_branch"],
        }

    repo_root = Path(inspected["repo_root"])
    removal = run_git(["worktree", "remove", "--force", inspected["worktree_path"]], repo_root)
    if removal.returncode != 0:
        stderr = removal.stderr.strip() or removal.stdout.strip() or "unknown git error"
        raise SystemExit(f"Failed to remove worktree: {stderr}")

    branch_deleted = False
    if inspected["worktree_branch_exists"]:
        delete_branch = run_git(["branch", "-D", inspected["worktree_branch"]], repo_root)
        branch_deleted = delete_branch.returncode == 0

    return {
        "action": "cleanup",
        "status": "ok",
        "run_id": run_id,
        "mode": mode,
        "decision": decision,
        "reason": reason,
        "worktree_path": inspected["worktree_path"],
        "worktree_branch": inspected["worktree_branch"],
        "branch_deleted": branch_deleted,
    }


def render_text(result: dict[str, Any]) -> str:
    action = result["action"]
    if action == "create":
        lines = [
            f"Run: {result['run_id']}",
            f"Repo root: {result['repo_root']}",
            f"Base branch: {result['base_branch']}",
            f"Base commit: {result.get('base_commit') or 'unknown'}",
            f"Worktree branch: {result['worktree_branch']}",
            f"Worktree path: {result['worktree_path']}",
            f"Cleanup policy: {result['cleanup_policy']}",
        ]
        if result.get("resumed"):
            lines.append("Create result: resumed existing worktree.")
        return "\n".join(lines)

    if action == "inspect":
        return "\n".join(
            [
                f"Run: {result['run_id']}",
                f"Worktree path: {result['worktree_path']}",
                f"Worktree branch: {result['worktree_branch']}",
                f"Path exists: {result['worktree_path_exists']}",
                f"Branch exists: {result['worktree_branch_exists']}",
                f"Has changes: {result['has_changes']}",
                f"Changed files: {result['changed_files']}",
                f"Commits ahead: {result['commits_ahead']}",
                f"HEAD: {result.get('head_commit') or 'unknown'}",
            ]
        )

    return "\n".join(
        [
            f"Run: {result['run_id']}",
            f"Cleanup mode: {result['mode']}",
            f"Decision: {result['decision']}",
            f"Reason: {result['reason']}",
            f"Worktree path: {result['worktree_path']}",
            f"Worktree branch: {result['worktree_branch']}",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create, inspect, or clean up a run worktree.")
    parser.add_argument("--repo", type=str, default=".", help="Repository path to operate on.")
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--base-branch",
        type=str,
        default=None,
        help="Base branch to use when creating or inspecting a worktree.",
    )
    parser.add_argument(
        "--cleanup-policy",
        type=str,
        default="keep_on_change",
        choices=sorted(VALID_CLEANUP_POLICIES),
    )
    parser.add_argument(
        "--dirty-repo-policy",
        type=str,
        default="reject",
        choices=sorted(VALID_DIRTY_REPO_POLICIES),
    )
    parser.add_argument("--branch-prefix", type=str, default=DEFAULT_BRANCH_PREFIX)
    parser.add_argument("--worktree-root", type=str, default=None)
    parser.add_argument(
        "--mode",
        type=str,
        default="auto",
        choices=sorted(VALID_CLEANUP_MODES),
        help="Cleanup mode for --cleanup.",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true")
    group.add_argument("--inspect", action="store_true")
    group.add_argument("--cleanup", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = Path(args.repo).expanduser()

    if args.create:
        if not args.base_branch:
            raise SystemExit("--base-branch is required for --create.")
        result = create_worktree_for_run(
            base_dir=base_dir,
            run_id=args.run_id,
            base_branch=args.base_branch,
            cleanup_policy=args.cleanup_policy,
            dirty_repo_policy=args.dirty_repo_policy,
            branch_prefix=args.branch_prefix,
            worktree_root_override=args.worktree_root,
        )
    elif args.inspect:
        result = inspect_worktree_for_run(
            base_dir=base_dir,
            run_id=args.run_id,
            base_branch=args.base_branch,
            branch_prefix=args.branch_prefix,
            worktree_root_override=args.worktree_root,
        )
    else:
        result = cleanup_worktree_for_run(
            base_dir=base_dir,
            run_id=args.run_id,
            mode=args.mode,
            base_branch=args.base_branch,
            branch_prefix=args.branch_prefix,
            worktree_root_override=args.worktree_root,
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
