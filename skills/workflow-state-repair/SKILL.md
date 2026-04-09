---
name: workflow-state-repair
description: Validate and repair malformed repo-local workflow state for the codex-coding-workflows plugin. Use when `.codex-workflows` looks invalid, when task-loop or verification files are malformed, when policy or memory schema drift is blocking planning or ship-readiness, or when Codex should create backups before normalizing workflow state files.
---

# Workflow State Repair

## Overview

Use this skill when repo-local workflow state cannot be trusted. It checks
`.codex-workflows/`, explains what is broken, and repairs malformed files while
creating backups first.

This skill repairs structure, not truth. It can normalize malformed files and preserve
what is salvageable, but it cannot invent a trustworthy task loop, real verification
evidence, durable facts that were never recorded, or missing worker conclusions for a
team run.

## Workflow

1. Inspect the current state.

```text
py -3 "./plugins/codex-coding-workflows/scripts/state_doctor.py" --check --json
```

2. Repair malformed files when it is safe to do so.

```text
py -3 "./plugins/codex-coding-workflows/scripts/state_doctor.py" --repair --json
```

3. Explain what was repaired versus what still needs human review.
   - malformed memory or policy files can be normalized automatically
   - malformed task loops can be normalized, but stale task content may still need review
   - malformed verification logs can be cleaned, but missing or stale evidence still requires a real verify run
   - malformed team manifests or event logs can be reconstructed and normalized
   - stale active team runs can be downgraded to `partial`
   - missing worker assignment files still require human review

## Guardrails

- Only mutate files inside `.codex-workflows/`.
- Always mention backup creation when repair runs.
- Do not claim that repair created fresh verification evidence or a trustworthy task plan by itself.
- Do not hide data loss. If malformed lines or sections were dropped, say so clearly.
