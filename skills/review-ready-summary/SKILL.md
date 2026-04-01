---
name: review-ready-summary
description: Generate a reviewer-facing summary from the current codex-coding-workflows state. Use when the user asks whether work is ready for review, wants a clean review summary, needs a PR-oriented artifact, or wants the plugin to turn task, verification, and ship-readiness state into a concise reviewer handoff.
---

# Review-Ready Summary

## Overview

Use this skill to generate a reviewer-facing summary from `.codex-workflows/`.
It should produce a concise artifact that explains what changed, what is
verified, what is still blocking review, and what the next reviewer-facing
actions are.

Treat the output as a review artifact, not a feel-good recap. It should help a reviewer
decide what to inspect next and what is still missing before review is credible.

## Workflow

1. Repair invalid state first.
   - If workflow state is malformed, route to `workflow-state-repair`.
2. Generate the report:

```text
py -3 "./plugins/codex-coding-workflows/scripts/report_builder.py" --mode review-ready --json
```

3. Use the generated markdown artifact as the primary summary surface.
   - Report path: `.codex-workflows/reports/review-ready-summary.md`
4. If the report shows stale or missing verification, route to `verify-change`
   before claiming the branch is actually review-ready.

## Output Requirements

The summary should stay concrete and reviewer-oriented:

- what changed
- what is verified
- what is still blocking review
- what the reviewer should inspect or ask for next

## Guardrails

- Do not claim a branch is ready just because the report exists.
- Treat the report as a generated artifact, not the source of truth.
- Keep reviewer-facing summaries concrete and blocker-oriented.
