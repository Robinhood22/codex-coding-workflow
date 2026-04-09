---
name: team-orchestration
description: Coordinate real subagent/team work through a conductor-owned run, durable repo-local state, and bounded worker assignments. Use when a workflow needs true parallel orchestration, explicit worker ownership, or a durable team-run artifact instead of ad hoc swarm prose.
---

# Team Orchestration

## Overview

Use this skill when a workflow needs real multi-agent coordination rather than a loose instruction to "swarm." This skill owns the orchestration pattern: conductor decisions, worker assignments, repo-local run state, synthesis, and graceful fallback when subagents are unavailable.

Read [references/live-subagent-protocol.md](references/live-subagent-protocol.md) when you need the exact live dispatch loop. Read [references/worker-output-template.md](references/worker-output-template.md) when you want workers to return a normalized markdown artifact the conductor can persist directly.

If the run is already prepared and the current Codex environment can actually spawn
subagents, hand off runtime execution to `../live-conductor/SKILL.md`.

This skill does not replace the specialist workflow that owns the domain task. It gives that workflow a reusable way to coordinate workers and persist the run under `.codex-workflows/teams/`.

Use this skill from another workflow when:

- the user explicitly asks for multi-agent, swarm, or team-based work
- parallel subagent work can materially reduce time or uncertainty
- the work naturally splits into disjoint responsibilities
- the conductor needs durable artifacts showing what each worker owned and returned

Do not use this skill for trivial one-step work or when the next action is blocked on a single urgent subtask that the main agent should just do directly.

## Core Model

The conductor owns the run.

- The conductor decides whether team mode is warranted.
- The conductor creates and updates repo-local run state.
- Workers receive bounded assignments and return structured outputs.
- Workers do not write `.codex-workflows/` directly.
- The conductor is responsible for synthesis, confidence calls, and final user-facing conclusions.

Repo-local run state lives under:

```text
.codex-workflows/teams/<run-id>/
```

Expected artifacts:

- `manifest.json`
- `plan.md`
- `events.jsonl`
- `workers/<worker-id>.md`
- `outputs/<worker-id>.md`
- `summary.md`

## Workflow

1. Decide whether team orchestration is justified.
   - Use single-agent execution when the task is small, tightly coupled, or blocked on one immediate next step.
   - Use team mode only when at least two bounded worker assignments can run without duplicating work.
2. Initialize the run.
   - In a repo-local install, create a run with:

```text
python3 "./scripts/team_state.py" --init-run --workflow <workflow-name> --owner-skill <skill-name> --goal "<goal>"
```

3. Write the conductor plan.
   - Record the goal, worker roster, and synthesis plan in `plan.md`.
   - Keep the plan short and execution-focused.
4. Assign workers.
   - Add only the minimum workers needed.
   - Give each worker:
     - a unique responsibility
     - a bounded scope
     - a clear output contract
     - any relevant dependencies on other workers
5. Spawn workers through the available subagent tools.
   - Prefer true parallel execution when available.
   - If the run is already prepared and you are actually about to drive live subagents,
     switch from planning/orchestration to `../live-conductor/SKILL.md`.
   - Prepare the whole run from one conductor brief with:

```text
python3 "./scripts/team_dispatch_brief.py" --run-id <run-id> --write
```

   - Build a ready-to-send worker packet with:

```text
python3 "./scripts/team_worker_packet.py" --run-id <run-id> --worker-id <worker-id> --json
```

   - Use the packet `prompt` as the subagent message.
   - Record the returned live agent id through the packet `status_update_command`.
   - Reuse `send_input` only when a running worker needs a bounded clarification.
   - Use `wait_agent` only when the conductor is genuinely blocked on worker output.
   - Close workers once their result has been persisted and they are no longer needed.
   - If subagents are unavailable, execute the same worker roles serially while preserving the same run artifacts.
6. Keep the conductor busy with non-overlapping work.
   - Do not idle just because workers are running.
   - Avoid duplicating worker responsibilities.
7. Record progress honestly.
   - Update worker status, output artifacts, and events as the run evolves.
   - Mark failures or partial results plainly.
8. Synthesize.
   - Combine worker outputs into one conductor-owned summary.
   - Call out contradictions, overlap, unresolved questions, and evidence quality.
9. Persist the final summary.
   - Write:

```text
python3 "./scripts/team_report.py" --run-id <run-id> --write
```

## Worker Contract

Every worker assignment should specify:

- goal
- responsibility boundaries
- what not to do
- expected artifact or output shape
- the file or surface area the conductor expects back

Every worker output should include:

- what was inspected or done
- concrete findings or returned work
- unresolved questions
- confidence or uncertainty where relevant

Use [references/worker-output-template.md](references/worker-output-template.md) when the domain workflow does not already define a stricter output shape.

Workers should not:

- mutate `.codex-workflows/`
- claim proof or consensus just because multiple workers agree
- silently broaden their assignment beyond what the conductor delegated

## Fallback Behavior

If true subagent execution is unavailable:

- keep the same conductor/worker structure
- run the worker roles serially in one thread
- still write assignments, outputs, events, and final summary
- mark the run as degraded or partial if the missing parallelism materially reduced confidence

Do not pretend real team execution happened if it did not.

## Output Requirements

At the end of an orchestrated run, the conductor should have:

- a valid run under `.codex-workflows/teams/<run-id>/`
- one output artifact per completed worker
- a summary artifact that explains worker outcomes and residual uncertainty
- a user-facing synthesis that reflects the real run status: complete, partial, failed, or cancelled

## Guardrails

- Do not create a team just because multiple roles sound impressive.
- Do not spawn workers with overlapping write ownership or duplicate analysis scope.
- Do not let workers mutate shared repo-local workflow state.
- Do not wait on workers reflexively when the conductor can still make progress.
- Do not report "parallel team orchestration" if the run actually degraded to serial execution.
- Do not confuse plugin-managed orchestration with native runtime-enforced agent isolation.
