---
name: execution-task-loop
description: Keep multi-step coding work organized with a live task loop. Use when the request has multiple parts, the task will take several steps, the scope may evolve during execution, or Codex needs to refresh task tracking after new discoveries or multi-file edits.
---

# Execution Task Loop

## Overview

Use this skill to keep active coding work organized. Standardize when to create or refresh a task list, keep exactly one active step at a time, and update the task loop as the implementation discovers new work.

Prefer existing Codex task primitives when they are available. Mirror the same discipline in `.codex-workflows/active-task-loop.md` so the repo keeps an inspectable source of truth.

## Operating Rules

- Exactly one step may be active at a time.
- A task loop is a live control surface, not a changelog.
- Refresh the loop as soon as reality changes. Do not wait until the end of the task.
- Verification and ship-readiness work must appear as explicit steps when they are required.

## Workflow

1. Start a task loop when the request is multi-step, stateful, or likely to branch.
2. Break work into concrete steps with one active item and the rest pending.
3. Update the loop immediately when:
   - a step finishes
   - a new blocker appears
   - scope expands or narrows
   - implementation uncovers follow-up work
4. Keep the loop honest.
   - Do not mark a step complete if verification for that step is still missing.
   - Remove or rewrite stale tasks when the plan changes.
   - Do not preserve outdated steps just because they were once written down.
5. Keep `.codex-workflows/active-task-loop.md` current.
   - Initialize state first if needed:

```text
py -3 "./plugins/codex-coding-workflows/scripts/memory_sync.py" --init --show
```

   - Update the task loop through `memory_sync.py` or by rewriting the file directly.
6. If the task loop is invalid instead of merely stale, route to `workflow-state-repair`
   before treating it as trustworthy state.

## Refresh Triggers

Refresh the task loop aggressively when the implementation stops matching the old list. Important triggers include:

- 3 or more changed files in the active change set
- a task loop that is stale or invalid
- significant scope changes discovered during exploration or coding
- new validation or follow-up steps created by failing checks

If the plugin script is available in a repo-local install, use it to sanity-check the current change scope:

```text
python "./plugins/codex-coding-workflows/scripts/analyze_change_scope.py" --json
```

On Windows systems where `python` is unavailable, use `py -3` instead.

## Output Requirements

The live task loop must:

- show exactly one active step
- keep remaining steps pending until they begin
- reflect new discoveries quickly
- stay synchronized with `.codex-workflows/active-task-loop.md`
- make verification or ship-audit work explicit instead of burying it in prose
- be short enough to drive execution without rereading a paragraph of explanation

## Guardrails

- Do not create a task loop for trivial one-step requests.
- Do not let the task loop become a stale changelog.
- Do not treat the task loop as a substitute for real verification.
