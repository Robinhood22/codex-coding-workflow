---
name: policy-risk-check
description: Assess the current change set against the codex-coding-workflows policy thresholds and explain delivery risk. Use only when the user explicitly wants a low/medium/high-risk call or a policy-based verification recommendation. Do not use this skill merely because a change is risky, and do not use it to perform the semantic code review itself.
---

# Policy Risk Check

## Overview

Use this skill to turn policy output into a concrete risk classification and verification recommendation. It is
advisory, not a permission system.

The goal is not to sound cautious. The goal is to explain why the current change is low,
medium, or high risk and whether policy implies stronger verification.

If the user is already asking for the review itself, route to `agentic-code-review`
instead of treating that request as a meta risk-classification question.

Direct review verbs are a hard stop for this skill:
- review
- inspect
- audit
- analyze this diff
- check this PR

Those requests belong to `agentic-code-review`, even when the change sounds risky.

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
   - `verify-change` for medium/high-risk work
   - `ship-readiness-audit` when the user is asking about landing or release readiness
   - `execution-task-loop` when the task loop is stale or missing
   - `project-memory-sync` when durable context changed
   - `workflow-state-repair` when policy or repo-local workflow state is invalid

## Guardrails

- Do not present policy as hard enforcement.
- Do not reduce risk to file-count alone; use the reasons from the script output.
- If policy falls back to defaults, say so.
- Do not call a change "safe" when the policy output is really just incomplete.
- Do not intercept direct review requests that should route to `agentic-code-review`.
