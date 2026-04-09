---
name: project-memory-sync
description: Maintain repo-local workflow memory and task state for the codex-coding-workflows plugin. Use when Codex should initialize `.codex-workflows`, record durable project facts, refresh the active task loop after scope changes, or inspect current workflow state before planning, verification, or ship-readiness work.
---

# Project Memory Sync

## Overview

Use this skill to keep repo-local workflow state current without pretending Codex core has
runtime-owned automatic memory. This skill owns `.codex-workflows/`.

Record only information that will still matter later: durable facts, stable constraints,
meaningful preferences, and decisions that should survive the current turn.

When team orchestration is in play, treat `.codex-workflows/teams/` as part of the same
inspectable state surface. Use it to understand the latest durable team run before
planning handoffs or claiming work is complete.

This skill now also owns plugin-managed shared memory:

- `memory.md`: local durable memory used for current-workspace recall
- `shared-memory.md`: repo-shared durable memory for teammates
- `memory-candidates.jsonl`: queued durable facts awaiting promotion
- `memory-sync-log.jsonl`: automatic/shared-memory sync history
- `buglog.jsonl`: explicit repo-local bug-fix recall
- `project-map.md`: optional focused repo map generated on demand

Local and shared memory should preserve these durable sections when they matter:

- `Stable Facts`
- `Preferences`
- `Constraints`
- `Open Questions`
- `Do-Not-Repeat`
- `Decision Log`

## Workflow

1. Initialize state when missing.
   - Run:

```text
py -3 "./plugins/codex-coding-workflows/scripts/memory_sync.py" --init --show
```

2. Record only durable facts.
   - Add stable project constraints, preferences, or decisions that will still matter later.
   - Use `Do-Not-Repeat` for repeated failure modes and `Decision Log` for durable implementation choices.
   - Do not store temporary debugging notes, speculation, raw logs, or provisional guesses.
   - Use local scope for personal or workspace-local durable context.
   - Use shared scope only for teammate-safe durable context.
   - Direct shared writes should mirror into local recall and leave a sync-log entry.
3. Queue facts when you are not ready to write them directly.
   - Add a candidate:

```text
python3 "./scripts/memory_sync.py" --append-memory-candidate "<fact>" --scope <local|shared> --section "Stable Facts" --candidate-source "<source>" --show
```

4. Refresh automatic/shared memory.
   - Promote queued candidates and mirror shared memory into local recall:

```text
python3 "./scripts/memory_sync.py" --auto-refresh --show
```

5. Refresh the task loop when the work changed shape.
   - Rewrite `active-task-loop.md` so it has exactly one active step.
   - Prefer replacing stale task loops over appending more clutter.
6. Inspect state before relying on it.
   - Use `--show` or `--json` to confirm whether task, verification, shared memory, or candidate state is stale or invalid.
7. Repair invalid state before trusting it.
   - If `memory.md`, `shared-memory.md`, `memory-candidates.jsonl`, `memory-sync-log.jsonl`,
     `buglog.jsonl`, `policy.json`, `active-task-loop.md`, or `verification-log.jsonl` is malformed,
     route to `workflow-state-repair`.
8. Generate a focused repo map when the codebase is unfamiliar or has shifted materially.
   - Run:

```text
python3 "./scripts/project_map.py" --generate
```

## Guardrails

- Treat `.codex-workflows/` as inspectable working state, not hidden automation.
- Do not write memory from hook reminders alone.
- Do not record secrets, tokens, or environment-specific credentials.
- Shared memory must pass secret scanning before it is queued or written.
- Do not confuse "this was mentioned once" with "this belongs in durable memory."
