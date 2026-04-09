---
name: agentic-code-review
description: Review a diff, PR, patch, or refactor for semantic correctness and hidden regressions. Use when the user asks to review code, inspect a risky change, audit a refactor, perform deep semantic review, or run multi-agent/swarm review. Trace changed behavior, inspect cross-file impact, search for counterexamples, and produce evidence-backed reviewer findings without overstating static analysis as proof.
---

# Agentic Code Review

## Overview

Use this skill to review code changes with semi-formal reasoning before or alongside execution-based verification. Focus on changed behavior, hidden call paths, invariants, edge cases, and counterexamples. Treat the output as a static review artifact, not proof.

Read [references/review-certificate.md](references/review-certificate.md) when you need the exact output shape. Read [references/swarm-roles.md](references/swarm-roles.md) when you need the role prompts for subagents. When team review is warranted, route orchestration through `../team-orchestration/SKILL.md` and keep this skill focused on review-specific scope, roles, and synthesis.

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
4. If swarm mode is warranted, initialize orchestration.
   - Use `team-orchestration` to create the run, register workers, and persist outputs before synthesis.
   - If the run is ready and the current environment exposes live subagent tools, activate `../live-conductor/SKILL.md` to actually dispatch the worker team.
   - In a repo-local install, bootstrap the review run with:

```text
python3 "./scripts/review_team_bootstrap.py" --goal "Review the current diff for semantic regressions" --review-question "<review question>" --intended-change "<intended change summary>" --json
```

   - Build each live worker dispatch packet with:

```text
python3 "./scripts/team_worker_packet.py" --run-id <run-id> --worker-id <worker-id> --json
```

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

Swarm mode should use `team-orchestration` so the run has a conductor, bounded worker assignments, repo-local run state, and a durable synthesis artifact. If subagents are unavailable, degrade to serial execution while preserving the same conductor-owned artifacts and making the degraded confidence explicit.

## Swarm Pattern

The main agent is the conductor. The conductor owns the final answer, decides whether the swarm is warranted, and is the only agent allowed to write repo-local state.

When swarm mode is selected:

1. initialize a team run under `.codex-workflows/teams/<run-id>/`
2. record bounded worker assignments using `review_team_bootstrap.py`
3. spawn workers through `team-orchestration`
4. prepare the conductor dispatch brief when you want the whole swarm packet set in one place:

```text
python3 "./scripts/team_dispatch_brief.py" --run-id <run-id> --write
```

5. build one packet per worker with `team_worker_packet.py`, then use the packet `prompt` with the live subagent tools
6. update worker status as they start:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --set-worker-status --worker-id <worker-id> --status running --agent-id <agent-id>
```

7. collect worker outputs and persist them:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --write-output --worker-id <worker-id> --output-file <worker-output-file> --summary "<one-line summary>" --confidence <0.0-1.0>
```

8. mark the run as synthesizing before conductor synthesis:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --set-run-status --status synthesizing --reason "Collecting worker outputs for final review synthesis"
```

9. synthesize them into the final review result
10. write the durable run summary:

```text
python3 "./scripts/team_report.py" --run-id <run-id> --write
```

11. close the run with the real outcome:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --set-run-status --status completed
```

Use `partial` when subagents were unavailable or one or more worker outputs are missing. Use `failed` when the review run itself broke down before a credible synthesis was possible.

When using subagents, keep the swarm small:
- `semantic-tracer`: trace changed behavior and cross-file call/data flow
- `regression-hunter`: search for counterexamples, edge cases, and broken assumptions
- `skeptic`: challenge unsupported claims, missing evidence, and overconfident conclusions

When the environment exposes live subagent tools:
- use the worker packet `recommended_agent_type` and `recommended_reasoning_effort` unless the review scope clearly needs a different setting
- use the packet `status_update_command` once the spawn returns a live agent id
- persist the result before closing the worker
- mark the run `partial` if you had to fall back to serial execution after planning a live swarm

Subagent rules:
- pass only task-local context: diff, relevant files, and the review question
- do not leak the expected answer or prior conclusions
- do not claim independence, consensus, or proof just because multiple agents agree
- do not let subagents mutate `.codex-workflows/`

## Review Workflow

1. Establish scope.
   - State the intended behavior change and the limits of static inspection.
2. Choose review mode.
   - Stay single-agent when the scope is small and coherent.
   - Use `team-orchestration` when swarm criteria are met.
3. If swarm mode is selected, bootstrap the team run and capture the returned `run_id`.
4. Build premises.
   - Name the changed functions, classes, configs, tests, and invariants that matter.
5. Trace behavior.
   - Follow the relevant call/data flow and inspect real definitions instead of relying on names.
6. Hunt for counterexamples.
   - Actively try to falsify claims that the diff is safe, equivalent, or complete.
7. Grade evidence.
   - Distinguish clearly between observed, inferred, and unverified claims.
8. Produce findings first.
   - Report only findings backed by code evidence or a tight inference chain.
9. Persist the orchestrated run status.
   - Update worker output artifacts as they finish.
   - Write the team summary through `team_report.py`.
10. Escalate when static review bottoms out.
   - Use `verify-change` when runtime behavior must be exercised.
   - Use `review-ready-summary` when the user wants a reviewer-facing artifact after review.

## Durable Artifacts

When the user wants durable workflow state, keep review artifacts separate from verification logs and separate from general team-run state.

1. If `.codex-workflows/` is relevant and missing, route to `project-memory-sync`.
2. Store orchestration artifacts under:

```text
.codex-workflows/teams/<run-id>/
```

3. Store review artifacts under:

```text
.codex-workflows/reviews/<review-id>/
```

4. Recommended review artifact set:
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
