---
name: codex-coding-workflows
description: Use when the user wants workflow discipline for a coding task, asks which review, planning, task tracking, verification, or ship-readiness flow to use, or wants a Claude-inspired coding workflow in Codex without pretending Codex core has features it does not expose today.
---

# Codex Coding Workflows

## Overview

Use this skill as the umbrella entrypoint for the plugin. Its job is to route quickly and then get out of the way. Do not solve the task from the umbrella layer when a specialist workflow is clearly the right tool.

Treat routing as precedence-based, not keyword voting. Route based on the work the user is asking you to do, not on adjectives attached to that work.

Route to one of these specialist workflows:

- `../agentic-code-review/SKILL.md`
- `../implementation-plan/SKILL.md`
- `../execution-task-loop/SKILL.md`
- `../project-memory-sync/SKILL.md`
- `../policy-risk-check/SKILL.md`
- `../workflow-state-repair/SKILL.md`
- `../verify-change/SKILL.md`
- `../review-ready-summary/SKILL.md`
- `../handoff-summary/SKILL.md`
- `../ship-readiness-audit/SKILL.md`

Keep the plugin orchestration-focused. Reuse Codex's existing tools and built-in skills instead of recreating their behavior inside this plugin.

Repo-local shared state lives in `.codex-workflows/`. Treat it as the source of truth for workflow memory, the active task loop, and verification evidence.

## Operating Rules

1. Route as soon as the workflow is clear.
2. If workflow state is invalid, say so early and route to `workflow-state-repair` before leaning on that state.
3. Do not keep multiple workflow skills mentally active when one is enough.
4. Do not invent runtime features that Codex does not actually expose.
5. Stop routing immediately when the user directly asks for review work and activate `agentic-code-review`.
6. End the umbrella phase with a short statement of which workflow is now active and why.

## Routing Rules

1. Use `agentic-code-review` for any direct request to review code, a diff, a PR, a patch, or a refactor. This includes risky diffs, deep review, semantic review, and explicit multi-agent/swarm review.
2. Use `policy-risk-check` only when the user is explicitly asking for a risk classification or policy-based verification recommendation.
3. Use `implementation-plan` when the user needs a decision-complete plan before coding.
4. Use `execution-task-loop` when the work is multi-step, stateful, or likely to evolve during implementation.
5. Use `project-memory-sync` when `.codex-workflows/` is missing, stale, or needs durable facts refreshed.
6. Use `workflow-state-repair` when repo-local workflow state is invalid or malformed.
7. Use `verify-change` after non-trivial work, especially before claiming a task is done.
8. Use `review-ready-summary` when the user wants a reviewer-facing artifact or asks whether work is ready for review.
9. Use `handoff-summary` when the user wants a teammate-facing status recap or handoff artifact.
10. Use `ship-readiness-audit` when the user asks whether a branch is ready for review, shipping, release, publish, or handoff.
11. If the user asks for "what should I use?" and the answer is obvious from repo state plus the request, choose for them and explain briefly.

Hard precedence:
- A direct review request always routes to `agentic-code-review`.
- A risk adjective does not override a review verb.
- `policy-risk-check` is advisory. It must not sit in front of an explicitly requested review.
- When the user asks both for review and risk, start with `agentic-code-review` and note whether `policy-risk-check` would still help afterward.

Forbidden routes:
- Do not route "review this risky refactor" to `policy-risk-check`.
- Do not route "inspect this PR" to `policy-risk-check`.
- Do not route "do a multi-agent semantic review" to `policy-risk-check`.
- Only use `policy-risk-check` when the user is asking for a classification or recommendation about risk/verification policy itself.

Examples:
- "Review this risky refactor" -> `agentic-code-review`
- "Review this risky refactor with a multi-agent semantic review" -> `agentic-code-review`
- "Inspect this PR for semantic regressions" -> `agentic-code-review`
- "How risky is this refactor?" -> `policy-risk-check`
- "Do I need stronger verification for this change?" -> `policy-risk-check`
- "Which workflow should I run before shipping?" -> `policy-risk-check` or `ship-readiness-audit`, depending on the request

## Default Workflow

1. Clarify the operating mode: review, planning, active execution tracking, memory/task sync, risk review, verification, artifact generation, or ship readiness.
2. If `.codex-workflows/` is relevant and missing, initialize it through `project-memory-sync`.
3. If repo-local workflow state is invalid, repair it through `workflow-state-repair` before relying on it.
4. Route to the narrowest matching specialist skill.
5. Generate review or handoff artifacts only when the user needs a durable summary instead of another transient answer.
6. Reuse existing Codex capabilities for review, QA, browsing, and ship workflows where they already fit.
7. Stop acting like a coordinator once the next workflow is chosen.

## Deferred Runtime Gaps

This plugin adds workflow discipline and discovery. It does not implement:

- automatic memory or persistent recall
- permission enforcement or new permission modes
- prompt-cache control or prompt-boundary tuning
- native specialist subagents with hard runtime tool boundaries
- IDE bridge, remote bridge, or mobile bridge behavior

Do not claim those runtime features exist just because this plugin is installed.
