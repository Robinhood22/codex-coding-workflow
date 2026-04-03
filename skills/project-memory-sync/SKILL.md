---
name: project-memory-sync
description: Maintain repo-local workflow memory and task state for the codex-coding-workflows plugin. Use when Codex should initialize `.codex-workflows`, record durable project facts, refresh the active task loop or task streams after scope changes, or inspect current workflow state before planning, verification, or ship-readiness work.
---

# Project Memory Sync

## Overview

Use this skill to keep repo-local workflow state current without pretending Codex has
automatic memory. This skill owns `.codex-workflows/`.

Record only information that will still matter later: durable facts, stable constraints,
meaningful preferences, and decisions that should survive the current turn.

## Workflow

1. Initialize state when missing.
   - Run:

```text
py -3 "./plugins/codex-coding-workflows/scripts/memory_sync.py" --init --show
```

2. Record only durable facts.
   - Add stable project constraints, preferences, or decisions that will still matter later.
   - Do not store temporary debugging notes, speculation, raw logs, or provisional guesses.
3. Refresh the task loop when the work changed shape.
   - In legacy mode, rewrite `active-task-loop.md` so it has exactly one active step.
   - In stream mode, update the relevant stream file and let the summary regenerate.
   - Prefer replacing stale task state over appending more clutter.
4. Inspect state before relying on it.
   - Use `--show` or `--json` to confirm whether the task loop or verification state is stale.
5. Repair invalid state before trusting it.
   - If `memory.md`, `policy.json`, `active-task-loop.md`, `task-streams/`, or `verification-log.jsonl`
     is malformed, route to `workflow-state-repair`.

## Guardrails

- Treat `.codex-workflows/` as inspectable working state, not hidden automation.
- Do not write memory from hook reminders alone.
- Do not record secrets, tokens, or environment-specific credentials.
- Do not confuse "this was mentioned once" with "this belongs in durable memory."
