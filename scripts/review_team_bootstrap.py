#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from team_state import add_worker, init_run


def normalize_multiline_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    items = [line.strip() for line in raw.splitlines()]
    return [item for item in items if item]


def build_assignment_text(
    role: str,
    goal: str,
    review_question: str | None,
    intended_change: str | None,
    changed_files: list[str],
) -> str:
    changed_files_block = "\n".join(f"- {path}" for path in changed_files) or "- [conductor will provide relevant files]"
    question_block = review_question or "Review the current change for semantic correctness and hidden regressions."
    intended_block = intended_change or "No explicit intended behavior summary was captured."

    role_specific = {
        "semantic-tracer": [
            "Trace changed behavior and cross-file call/data flow.",
            "Focus on entrypoints, invoked definitions, changed guards/defaults, and downstream outputs.",
            "Return traced paths, concrete evidence, and any unresolved semantic gaps.",
        ],
        "regression-hunter": [
            "Search for counterexamples, edge cases, hidden callers, config/default drift, and missing coverage.",
            "Try to falsify claims that the change is safe, equivalent, or complete.",
            "Return concrete risky scenarios, the strongest counterexample found, and what you checked but could not falsify.",
        ],
        "skeptic": [
            "Challenge unsupported claims, overconfidence, missing evidence, and runtime assumptions.",
            "Look for unjustified leaps, overreliance on naming/comments, and third-party or runtime gaps.",
            "Return questionable claims, confidence adjustments, and what should be marked UNVERIFIED.",
        ],
    }.get(
        role,
        [
            "Complete the assigned review work within the bounded scope below.",
            "Return concrete findings and unresolved questions.",
        ],
    )

    lines = [
        f"# {role}",
        "",
        "## Goal",
        goal,
        "",
        "## Review Question",
        question_block,
        "",
        "## Intended Behavior Change",
        intended_block,
        "",
        "## Changed Files",
        changed_files_block,
        "",
        "## Assignment",
    ]
    lines.extend(f"- {item}" for item in role_specific)
    lines.extend(
        [
            "",
            "## Constraints",
            "- Do not mutate `.codex-workflows/`.",
            "- Stay within your assigned responsibility and avoid duplicating other worker roles.",
            "- Return concrete evidence and call out uncertainty explicitly.",
            "",
            "## Output Contract",
            "- What you inspected",
            "- Concrete findings or returned work",
            "- Unresolved questions",
            "- Confidence or uncertainty where relevant",
            "",
        ]
    )
    return "\n".join(lines)


def bootstrap_review_team(
    base_dir: Path,
    goal: str,
    review_question: str | None,
    intended_change: str | None,
    changed_files: list[str],
    run_id: str | None = None,
) -> dict[str, Any]:
    run_summary = init_run(
        base_dir=base_dir,
        workflow="agentic-code-review",
        goal=goal,
        owner_skill="agentic-code-review",
        run_id=run_id,
        mode="team",
    )
    resolved_run_id = str(run_summary["run_id"])

    worker_specs = [
        ("semantic-tracer", "semantic-tracer", "Trace changed behavior and cross-file call/data flow."),
        ("regression-hunter", "regression-hunter", "Search for counterexamples, edge cases, hidden callers, and drift."),
        ("skeptic", "skeptic", "Challenge unsupported claims, missing evidence, and overconfidence."),
    ]

    for worker_id, role, responsibility in worker_specs:
        add_worker(
            base_dir=base_dir,
            run_id=resolved_run_id,
            worker_id=worker_id,
            role=role,
            responsibility=responsibility,
            depends_on=[],
            assignment_text=build_assignment_text(
                role=role,
                goal=goal,
                review_question=review_question,
                intended_change=intended_change,
                changed_files=changed_files,
            ),
        )

    return {
        "run_id": resolved_run_id,
        "workflow": "agentic-code-review",
        "goal": goal,
        "workers": [worker_id for worker_id, _, _ in worker_specs],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap a conductor-owned team run for agentic code review."
    )
    parser.add_argument("--repo", type=str, default=".")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--goal", type=str, required=True)
    parser.add_argument("--review-question", type=str, default=None)
    parser.add_argument("--intended-change", type=str, default=None)
    parser.add_argument(
        "--changed-files",
        type=str,
        default=None,
        help="Optional newline-delimited changed files to include in worker assignments.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = Path(args.repo).expanduser()

    result = bootstrap_review_team(
        base_dir=base_dir,
        goal=args.goal,
        review_question=args.review_question,
        intended_change=args.intended_change,
        changed_files=normalize_multiline_list(args.changed_files),
        run_id=args.run_id,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Run: {result['run_id']}")
        print("Workers:")
        for worker_id in result["workers"]:
            print(f"- {worker_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
