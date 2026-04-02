---
name: agentic-code-review
description: Review a diff, PR, patch, or refactor for semantic correctness and hidden regressions. Use when the user asks to review code, inspect a risky change, audit a refactor, perform deep semantic review, or run multi-agent/swarm review. Trace changed behavior, inspect cross-file impact, search for counterexamples, and produce evidence-backed reviewer findings without overstating static analysis as proof.
---

# Agentic Code Review

## Overview

Use this skill to review code changes with semi-formal reasoning before or alongside execution-based verification. Focus on changed behavior, hidden call paths, invariants, edge cases, and counterexamples. Treat the output as a static review artifact, not proof.

Read [references/review-certificate.md](references/review-certificate.md) when you need the exact output shape. Read [references/swarm-roles.md](references/swarm-roles.md) when you need the role prompts for subagents.

## Scope First

1. Inspect the change scope before choosing a review mode.
   - If the plugin script is available in a repo-local install, run:

```text
python "./plugins/codex-coding-workflows/scripts/analyze_change_scope.py" --json
```

   - When working inside this plugin repo directly, use:

```text
python3 "./scripts/analyze_change_scope.py" --json
```

2. Identify:
   - changed files
   - intended behavior change
   - highest-risk semantic paths
   - whether static review can answer the core question or must escalate

3. Keep review and verification distinct.
   - Static review is allowed to inspect code and infer risk.
   - Static review is not execution-based verification.
   - Route to `verify-change` when runtime evidence is required.

## Review Mode

Choose the lightest mode that can answer the question credibly.

### Single-Agent Review

Use a single agent when:
- the diff is small or clearly low-risk
- the user wants a quick semantic review
- the core behavior fits within one coherent trace

### Swarm Review

Use swarm mode when one or more are true:
- the user explicitly asks for multi-agent, swarm, or deep review
- the diff is medium/high risk
- `3+` files changed
- the change affects auth, validation, config, defaults, or exception handling
- the diff claims refactor-equivalence
- cross-file semantic uncertainty remains after initial inspection

Swarm mode is best-effort, not guaranteed. If subagents are unavailable, run the same roles serially in one thread.

## Swarm Pattern

The main agent is the conductor. The conductor owns the final answer, decides whether the swarm is warranted, and is the only agent allowed to write repo-local state.

When using subagents, keep the swarm small:
- `semantic-tracer`: trace changed behavior and cross-file call/data flow
- `regression-hunter`: search for counterexamples, edge cases, and broken assumptions
- `skeptic`: challenge unsupported claims, missing evidence, and overconfident conclusions

Subagent rules:
- pass only task-local context: diff, relevant files, and the review question
- do not leak the expected answer or prior conclusions
- do not claim independence, consensus, or proof just because multiple agents agree
- do not let subagents mutate `.codex-workflows/`

## Review Workflow

1. Establish scope.
   - State the intended behavior change and the limits of static inspection.
2. Build premises.
   - Name the changed functions, classes, configs, tests, and invariants that matter.
3. Trace behavior.
   - Follow the relevant call/data flow and inspect real definitions instead of relying on names.
4. Hunt for counterexamples.
   - Actively try to falsify claims that the diff is safe, equivalent, or complete.
5. Grade evidence.
   - Distinguish clearly between observed, inferred, and unverified claims.
6. Produce findings first.
   - Report only findings backed by code evidence or a tight inference chain.
7. Escalate when static review bottoms out.
   - Use `verify-change` when runtime behavior must be exercised.
   - Use `review-ready-summary` when the user wants a reviewer-facing artifact after review.

## Durable Artifacts

When the user wants durable workflow state, keep review artifacts separate from verification logs.

1. If `.codex-workflows/` is relevant and missing, route to `project-memory-sync`.
2. Store review artifacts under:

```text
.codex-workflows/reviews/<review-id>/
```

3. Recommended artifact set:
   - `scope.md`
   - `semantic-trace.md`
   - `regression-hunt.md`
   - `skeptic-pass.md`
   - `summary.md`

Only the conductor writes these files. Subagents should return findings to the conductor instead of mutating repo-local state directly.

## Guardrails

- Do not treat natural-language reasoning as formal proof.
- Do not replace tests with review.
- Do not use swarm mode for tiny cosmetic diffs.
- Do not hide uncertainty behind polished prose.
- Do not report style-only nits unless they affect correctness, maintainability, or reviewer trust.
- If third-party or runtime semantics matter and you cannot inspect them credibly, say `UNVERIFIED`.
