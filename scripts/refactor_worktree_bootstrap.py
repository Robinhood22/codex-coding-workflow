#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from team_state import (
    add_worker,
    append_team_event,
    generate_run_id,
    get_team_run_paths,
    init_run,
)
from worktree_manager import create_worktree_for_run, write_execution_metadata


def now_timestamp() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def normalize_multiline_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    items = [line.strip() for line in raw.splitlines()]
    return [item for item in items if item]


def render_list_block(items: list[str], fallback: str) -> str:
    if not items:
        return f"- {fallback}"
    return "\n".join(f"- {item}" for item in items)


def build_execution_record(
    run_id: str,
    worktree_result: dict[str, Any],
    cleanup_policy: str,
    dirty_repo_policy: str,
) -> dict[str, Any]:
    timestamp = now_timestamp()
    return {
        "schema_version": 1,
        "run_id": run_id,
        "execution_mode": "worktree",
        "status": "active",
        "repo_root": worktree_result["repo_root"],
        "base_branch": worktree_result["base_branch"],
        "base_commit": worktree_result["base_commit"],
        "worktree_branch": worktree_result["worktree_branch"],
        "worktree_path": worktree_result["worktree_path"],
        "created_at": timestamp,
        "updated_at": timestamp,
        "cleanup_policy": cleanup_policy,
        "cleanup_status": "pending",
        "dirty_repo_policy": dirty_repo_policy,
        "notes": [],
        "last_verified_head": None,
    }


def build_implementer_assignment_text(
    goal: str,
    intended_change: str | None,
    constraints: list[str],
    changed_files: list[str],
    execution: dict[str, Any],
) -> str:
    lines = [
        "# refactor-implementer",
        "",
        "## Goal",
        goal,
        "",
        "## Intended Change",
        intended_change or "No explicit intended-change summary was provided.",
        "",
        "## Worktree",
        f"- Worktree path: {execution['worktree_path']}",
        f"- Worktree branch: {execution['worktree_branch']}",
        f"- Base branch: {execution['base_branch']}",
        f"- Base commit: {execution['base_commit']}",
        "",
        "## Candidate Changed Files",
        render_list_block(changed_files, "Conductor did not pre-seed changed files."),
        "",
        "## Constraints",
        "- Work only inside the worktree path above.",
        "- Do not edit the main repository checkout.",
        "- Do not mutate `.codex-workflows/`; the conductor owns workflow state.",
        "- Prefer behavior-preserving structural changes before opportunistic cleanup.",
    ]
    if constraints:
        lines.extend(f"- {item}" for item in constraints)
    lines.extend(
        [
            "",
            "## Assignment",
            "- Complete the requested refactor within the isolated worktree.",
            "- Keep behavioral changes explicit; do not silently broaden scope.",
            "- Call out any risky deltas, migrations, or follow-up work.",
            "",
            "## Output Contract",
            "- What changed",
            "- Files touched",
            "- Risk or migration notes",
            "- Confidence",
            "",
        ]
    )
    return "\n".join(lines)


def build_verifier_assignment_text(
    goal: str,
    intended_change: str | None,
    constraints: list[str],
    changed_files: list[str],
    execution: dict[str, Any],
) -> str:
    lines = [
        "# refactor-verifier",
        "",
        "## Goal",
        f"Verify the refactor goal: {goal}",
        "",
        "## Intended Change",
        intended_change or "No explicit intended-change summary was provided.",
        "",
        "## Worktree",
        f"- Verify only against: {execution['worktree_path']}",
        f"- Worktree branch: {execution['worktree_branch']}",
        f"- Base branch: {execution['base_branch']}",
        "",
        "## Candidate Changed Files",
        render_list_block(changed_files, "Conductor did not pre-seed changed files."),
        "",
        "## Constraints",
        "- Do not verify against the main repository checkout.",
        "- Do not mutate `.codex-workflows/`; the conductor owns workflow state.",
        "- Treat missing checks or unverified behavior as PARTIAL, not PASS.",
    ]
    if constraints:
        lines.extend(f"- {item}" for item in constraints)
    lines.extend(
        [
            "",
            "## Assignment",
            "- Review the implementer's work inside the isolated worktree.",
            "- Run targeted checks or spot checks that support the behavior-preserving claim.",
            "- Surface regressions, weak evidence, or verification gaps explicitly.",
            "",
            "## Output Contract",
            "- Commands run",
            "- Observed results",
            "- Regressions found or not found",
            "- PASS, FAIL, or PARTIAL with confidence",
            "",
        ]
    )
    return "\n".join(lines)


def write_refactor_plan(
    base_dir: Path,
    run_id: str,
    goal: str,
    intended_change: str | None,
    constraints: list[str],
    changed_files: list[str],
    execution: dict[str, Any],
) -> Path:
    plan_path = get_team_run_paths(base_dir, run_id)["plan"]
    lines = [
        "# Team Run Plan",
        "",
        f"Goal: {goal}",
        "",
        f"Workflow: worktree-refactor",
        f"Worktree path: {execution['worktree_path']}",
        f"Worktree branch: {execution['worktree_branch']}",
        f"Base branch: {execution['base_branch']}",
        "",
        "Workers:",
        "- refactor-implementer",
        "- refactor-verifier (depends on refactor-implementer)",
        "",
        "Intended change:",
        intended_change or "- None provided.",
        "",
        "Constraints:",
        render_list_block(constraints, "No extra constraints provided."),
        "",
        "Changed files hint:",
        render_list_block(changed_files, "No changed files were pre-seeded."),
        "",
    ]
    plan_path.write_text("\n".join(lines), encoding="utf-8")
    return plan_path


def bootstrap_refactor_worktree(
    base_dir: Path,
    goal: str,
    base_branch: str,
    intended_change: str | None,
    constraints: list[str],
    changed_files: list[str],
    cleanup_policy: str,
    dirty_repo_policy: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    resolved_run_id = run_id or generate_run_id("worktree-refactor")
    run_paths = get_team_run_paths(base_dir, resolved_run_id)
    if run_paths["manifest"].exists():
        raise SystemExit(f"Run {resolved_run_id!r} already exists.")

    worktree_result = create_worktree_for_run(
        base_dir=base_dir,
        run_id=resolved_run_id,
        base_branch=base_branch,
        cleanup_policy=cleanup_policy,
        dirty_repo_policy=dirty_repo_policy,
    )

    init_run(
        base_dir=base_dir,
        workflow="worktree-refactor",
        goal=goal,
        owner_skill="worktree-refactor",
        run_id=resolved_run_id,
        mode="team",
    )

    execution = build_execution_record(
        run_id=resolved_run_id,
        worktree_result=worktree_result,
        cleanup_policy=cleanup_policy,
        dirty_repo_policy=dirty_repo_policy,
    )
    execution_path = write_execution_metadata(base_dir, resolved_run_id, execution)
    plan_path = write_refactor_plan(
        base_dir=base_dir,
        run_id=resolved_run_id,
        goal=goal,
        intended_change=intended_change,
        constraints=constraints,
        changed_files=changed_files,
        execution=execution,
    )

    add_worker(
        base_dir=base_dir,
        run_id=resolved_run_id,
        worker_id="refactor-implementer",
        role="refactor-implementer",
        responsibility="Perform the refactor inside the isolated worktree.",
        depends_on=[],
        assignment_text=build_implementer_assignment_text(
            goal=goal,
            intended_change=intended_change,
            constraints=constraints,
            changed_files=changed_files,
            execution=execution,
        ),
    )
    add_worker(
        base_dir=base_dir,
        run_id=resolved_run_id,
        worker_id="refactor-verifier",
        role="refactor-verifier",
        responsibility="Verify the refactor inside the isolated worktree.",
        depends_on=["refactor-implementer"],
        assignment_text=build_verifier_assignment_text(
            goal=goal,
            intended_change=intended_change,
            constraints=constraints,
            changed_files=changed_files,
            execution=execution,
        ),
    )

    append_team_event(
        base_dir=base_dir,
        run_id=resolved_run_id,
        event={
            "type": "conductor_note",
            "actor": "conductor",
            "details": {
                "message": (
                    "Created isolated refactor worktree "
                    f"{execution['worktree_path']} on {execution['worktree_branch']}."
                )
            },
        },
    )

    return {
        "run_id": resolved_run_id,
        "workflow": "worktree-refactor",
        "goal": goal,
        "worktree": {
            "repo_root": execution["repo_root"],
            "worktree_path": execution["worktree_path"],
            "worktree_branch": execution["worktree_branch"],
            "base_branch": execution["base_branch"],
            "base_commit": execution["base_commit"],
        },
        "execution_path": str(execution_path),
        "plan_path": str(plan_path),
        "workers": ["refactor-implementer", "refactor-verifier"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap an isolated worktree-backed team run for a large refactor."
    )
    parser.add_argument("--repo", type=str, default=".")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--goal", type=str, required=True)
    parser.add_argument("--base-branch", type=str, default="main")
    parser.add_argument("--intended-change", type=str, default=None)
    parser.add_argument(
        "--constraints",
        type=str,
        default=None,
        help="Optional newline-delimited constraints to include in worker assignments.",
    )
    parser.add_argument(
        "--changed-files",
        type=str,
        default=None,
        help="Optional newline-delimited changed-file hints to include in worker assignments.",
    )
    parser.add_argument(
        "--cleanup-policy",
        type=str,
        default="keep_on_change",
        choices=["keep_on_change", "remove_if_clean", "keep_always", "remove_always"],
    )
    parser.add_argument(
        "--dirty-repo-policy",
        type=str,
        default="reject",
        choices=["reject", "allow"],
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = Path(args.repo).expanduser()

    result = bootstrap_refactor_worktree(
        base_dir=base_dir,
        goal=args.goal,
        base_branch=args.base_branch,
        intended_change=args.intended_change,
        constraints=normalize_multiline_list(args.constraints),
        changed_files=normalize_multiline_list(args.changed_files),
        cleanup_policy=args.cleanup_policy,
        dirty_repo_policy=args.dirty_repo_policy,
        run_id=args.run_id,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Run: {result['run_id']}")
        print(f"Worktree: {result['worktree']['worktree_path']}")
        print("Workers:")
        for worker_id in result["workers"]:
            print(f"- {worker_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
