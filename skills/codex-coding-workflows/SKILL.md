---
name: codex-coding-workflows
description: Use when the user wants workflow discipline for a coding task, asks which planning, task tracking, verification, or ship-readiness flow to use, or wants a Claude-inspired coding workflow in Codex without pretending Codex core has features it does not expose today.
---

# Codex Coding Workflows

## Overview

Use this skill as the umbrella entrypoint for the plugin. Its job is to route quickly and then get out of the way. Do not solve the task from the umbrella layer when a specialist workflow is clearly the right tool.

Route to one of these specialist workflows:

- `../implementation-plan/SKILL.md`
- `../execution-task-loop/SKILL.md`
- `../project-memory-sync/SKILL.md`
- `../risk-review/SKILL.md`
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
5. End the umbrella phase with a short statement of which workflow is now active and why.

## Routing Rules

1. Use `implementation-plan` when the user needs a decision-complete plan before coding.
2. Use `execution-task-loop` when the work is multi-step, stateful, or likely to evolve during implementation.
3. Use `project-memory-sync` when `.codex-workflows/` is missing, stale, or needs durable facts refreshed.
4. Use `risk-review` when the user needs an explicit low/medium/high-risk call or a workflow recommendation based on policy.
5. Use `workflow-state-repair` when repo-local workflow state is invalid or malformed.
6. Use `verify-change` after non-trivial work, especially before claiming a task is done.
7. Use `review-ready-summary` when the user wants a reviewer-facing artifact or asks whether work is ready for review.
8. Use `handoff-summary` when the user wants a teammate-facing status recap or handoff artifact.
9. Use `ship-readiness-audit` when the user asks whether a branch is ready for review, shipping, release, publish, or handoff.
10. If the user asks for "what should I use?" and the answer is obvious from repo state plus the request, choose for them and explain briefly.

## Default Workflow

1. Clarify the operating mode: planning, active execution tracking, memory/task sync, risk review, verification, artifact generation, or ship readiness.
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
