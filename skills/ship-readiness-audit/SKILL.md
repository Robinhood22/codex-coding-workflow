---
name: ship-readiness-audit
description: Audit whether the current branch is ready for review, publish, release, or ship handoff. Use when the user asks what is left before shipping, whether a branch is ready, or which obvious blockers remain across git state, diff scope, tests, and release-adjacent checks.
---

# Ship Readiness Audit

## Overview

Use this skill when the user asks whether work is ready for review, publish, release, or ship. Audit the branch state first, then summarize blockers and confidence without pushing, publishing, or creating releases unless the user asks for that separately.

This is a blocker audit, not a celebratory summary. Separate what is done, what is missing,
and what is risky. If the branch is not ready, say exactly why and name the smallest next
actions that would move it forward.

## Workflow

1. Start with branch state.
   - In a repo-local install, run:

```text
python "./plugins/codex-coding-workflows/scripts/branch_readiness.py" --json
```

If `python` is unavailable on Windows, use `py -3` instead.

2. Review policy and workflow state when the branch is non-trivial.
   - If needed, run:

```text
py -3 "./plugins/codex-coding-workflows/scripts/policy_check.py" --intent ship --json
```

3. Interpret the obvious blockers by category.
   - git state: dirty working tree, no upstream branch, branch behind upstream, or nothing to ship
   - workflow state: missing, stale, or invalid task loop and malformed state files
   - verification evidence: missing, stale, or invalid verification logs
   - release readiness: missing validation, missing review artifact, or unresolved risky gaps
4. Inspect the actual change scope.
   - use git status, diff, and recent commits
   - note whether tests or validation steps still appear missing
   - use `verification_summary.py` or the audit output to understand whether evidence is
     fresh, missing, or malformed
5. Reuse existing Codex capabilities when they are the right next action.
   - review workflows for PR readiness
   - workflow-state-repair when repo-local state is invalid
   - review-ready-summary when the branch needs a reviewer-facing artifact
   - handoff-summary when the branch needs a teammate/operator handoff artifact
   - verification workflows for confidence before finalizing
   - ship workflows only after the audit is complete and the user asks for execution
6. End with the smallest next actions that would materially improve readiness.

## Output Requirements

Return:

- current branch and upstream state
- changed-file and working-tree summary
- workflow-state summary from `.codex-workflows/`
- blockers grouped into done, missing, and risky
- the smallest next actions to become ship-ready

Avoid generic advice. Make every blocker concrete and actionable.

## Guardrails

- Do not publish, push, or create a release from this skill alone.
- Do not pretend git state is clean when the audit shows otherwise.
- Do not confuse "tests exist" with "this branch is ready."
