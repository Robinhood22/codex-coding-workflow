#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyze_change_scope import analyze_change_scope
from workflow_state import load_policy, verification_required_for


def build_policy_review(base_dir: Path, intent: str) -> dict[str, Any]:
    scope = analyze_change_scope(base_dir)
    policy_info = load_policy(base_dir)
    policy = policy_info["data"]
    risk_level = scope.get("risk_level", "low")
    verification_required = verification_required_for(risk_level, policy)

    next_skills: list[str] = []
    if scope.get("task_loop_needed") or scope.get("task_loop_stale"):
        next_skills.append("execution-task-loop")
    if scope.get("memory_refresh_needed"):
        next_skills.append("project-memory-sync")
    if risk_level in {"medium", "high"}:
        next_skills.append("policy-risk-check")
    if verification_required:
        next_skills.append("verify-change")
    if intent == "ship":
        next_skills.append("ship-readiness-audit")

    deduped_skills: list[str] = []
    for skill in next_skills:
        if skill not in deduped_skills:
            deduped_skills.append(skill)

    return {
        "intent": intent,
        "risk_level": risk_level,
        "reasons": scope.get("risk_reasons", []),
        "verification_required": verification_required,
        "memory_refresh_needed": scope.get("memory_refresh_needed", False),
        "task_loop_stale": scope.get("task_loop_stale", False),
        "recommended_skills": deduped_skills,
        "policy": {
            "path": policy_info["path"],
            "used_default": policy_info["used_default"],
            "errors": policy_info["errors"],
        },
        "scope": scope,
    }


def render_text(review: dict[str, Any]) -> str:
    lines = [
        f"Intent: {review['intent']}",
        f"Risk level: {review['risk_level']}",
        "Reasons:",
    ]
    lines.extend(f"- {reason}" for reason in review["reasons"] or ["No risk reasons recorded."])
    lines.append(
        "Verification required: "
        + ("yes" if review["verification_required"] else "no")
    )
    lines.append(
        "Memory refresh needed: "
        + ("yes" if review["memory_refresh_needed"] else "no")
    )
    if review["recommended_skills"]:
        lines.append("Recommended skills:")
        lines.extend(f"- {skill}" for skill in review["recommended_skills"])
    if review["policy"]["errors"]:
        lines.append("Policy notes:")
        lines.extend(f"- {error}" for error in review["policy"]["errors"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply workflow policy to the current change scope.")
    parser.add_argument("--repo", type=str, default=".", help="Repository or workspace path to inspect.")
    parser.add_argument("--intent", type=str, default="general", help="Current workflow intent, such as plan, verify, or ship.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    review = build_policy_review(Path(args.repo).expanduser(), args.intent)
    if args.json:
        print(json.dumps(review, indent=2))
    else:
        print(render_text(review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
