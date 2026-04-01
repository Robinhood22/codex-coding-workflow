---
name: risk-review
description: Review the current change set against the codex-coding-workflows policy thresholds and explain the real delivery risk. Use when Codex should decide whether a change is low, medium, or high risk, whether verification is required, or which workflow skill should run next before review or shipping.
---

# Risk Review

## Overview

Use this skill to turn raw diff size into a concrete workflow recommendation. It is
advisory, not a permission system.

The goal is not to sound cautious. The goal is to explain why the current change is low,
medium, or high risk and which workflow should run next.

## Workflow

1. Run the policy check.

```text
py -3 "./plugins/codex-coding-workflows/scripts/policy_check.py" --json
```

2. Explain the result in plain language.
   - state the risk level
   - explain why the change reached that level
   - say whether verification is required by policy
   - say whether policy defaults were used
3. Recommend the next workflow step.
   - `execution-task-loop` when the task loop is stale or missing
   - `project-memory-sync` when durable context changed
   - `workflow-state-repair` when policy or repo-local workflow state is invalid
   - `verify-change` for medium/high-risk work
   - `ship-readiness-audit` when the user is asking about landing or release readiness

## Guardrails

- Do not present policy as hard enforcement.
- Do not reduce risk to file-count alone; use the reasons from the script output.
- If policy falls back to defaults, say so.
- Do not call a change "safe" when the policy output is really just incomplete.
