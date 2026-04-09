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

    next_skills: list[str] = list(scope.get("recommended_skills", []))
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
        "micro_reasoning_recommended": scope.get("micro_reasoning_recommended", False),
        "reasoning_hotspots": scope.get("reasoning_hotspots", []),
        "micro_reasoning_escalation_recommended": scope.get(
            "micro_reasoning_escalation_recommended",
            False,
        ),
        "recurring_reasoning_hotspots": scope.get("recurring_reasoning_hotspots", []),
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
    lines.append(
        "Micro reasoning recommended: "
        + ("yes" if review.get("micro_reasoning_recommended") else "no")
    )
    lines.append(
        "Hotspot escalation recommended: "
        + ("yes" if review.get("micro_reasoning_escalation_recommended") else "no")
    )
    if review.get("reasoning_hotspots"):
        lines.append("Hotspots:")
        lines.extend(f"- {item['summary']}" for item in review["reasoning_hotspots"])
    if review.get("recurring_reasoning_hotspots"):
        lines.append("Recurring hotspots:")
        for item in review["recurring_reasoning_hotspots"]:
            skills = ", ".join(item.get("recommended_skills", []))
            if skills:
                lines.append(f"- {item['summary']} Escalate with: {skills}.")
            else:
                lines.append(f"- {item['summary']}")
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
