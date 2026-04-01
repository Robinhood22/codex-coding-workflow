---
name: handoff-summary
description: Generate a teammate-facing handoff summary from the current codex-coding-workflows state. Use when the user wants a status handoff, asks what the next person should know, needs a concise project-state recap, or wants a durable markdown artifact that combines memory, task, verification, and open-work context.
---

# Handoff Summary

## Overview

Use this skill to generate a teammate-facing handoff artifact from
`.codex-workflows/`. The result should explain what changed, what is verified,
what remains open, and what the next person should do.

Treat the output as an operational handoff, not a narrative recap. The next person
should be able to pick up the work without rereading the whole thread.

## Workflow

1. Repair invalid state first.
   - If workflow state is malformed, route to `workflow-state-repair`.
2. Generate the report:

```text
py -3 "./plugins/codex-coding-workflows/scripts/report_builder.py" --mode handoff --json
```

3. Use the generated markdown artifact as the primary handoff surface.
   - Report path: `.codex-workflows/reports/handoff-summary.md`
4. If the report exposes stale task or verification state, route to
   `execution-task-loop`, `project-memory-sync`, or `verify-change` as needed.

## Output Requirements

The handoff should stay focused on:

- what changed
- what is verified
- what is still open or risky
- durable constraints the next person must not miss
- the smallest next actions

## Guardrails

- Do not invent completed work that does not exist in workflow state.
- Treat the report as a generated snapshot, not persistent truth.
- Keep the handoff focused on what the next person needs to know and do.
