---
name: live-conductor
description: Drive an existing team run through live Codex subagent tools while keeping `.codex-workflows/teams/` in sync. Use when a team run already exists, Codex can spawn subagents in this environment, and the conductor should execute the run instead of degrading to serial work.
---

# Live Conductor

## Overview

Use this skill when the team run already exists and Codex can actually drive live
subagents in the current environment. This skill is the runtime adapter layer between
repo-local orchestration artifacts and Codex subagent tools.

It does not replace the domain workflow. It executes the run that another workflow
planned. Typical pairings:

- `agentic-code-review` owns the review question and synthesis
- `team-orchestration` owns the general conductor pattern
- `live-conductor` owns the actual `spawn_agent` / `send_input` / `wait_agent` /
  `close_agent` loop for a prepared run

Use this skill only when:

- a run already exists under `.codex-workflows/teams/<run-id>/`
- the current Codex environment exposes live subagent tools
- the conductor is ready to actually dispatch workers now

Do not use this skill for planning-only work or when the current environment cannot
spawn subagents. In that case, stay with `team-orchestration` and degrade honestly.

Read [references/live-loop.md](references/live-loop.md) for the exact loop.

## Inputs

Before activating this skill, the conductor should already have:

- `run_id`
- a prepared run under `.codex-workflows/teams/<run-id>/`
- worker assignments written under `workers/`
- any execution metadata already written under `execution.json`
- a clear domain workflow that owns the final synthesis

Useful repo-local commands:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --show-run --json
python3 "./scripts/team_dispatch_brief.py" --run-id <run-id> --write --json
```

## Workflow

1. Inspect the run.
   - Confirm the run exists and identify pending, running, completed, failed, and cancelled workers.
   - Do not respawn workers that already have credible completed outputs.
2. Build the conductor brief.
   - Generate the dispatch brief:

```text
python3 "./scripts/team_dispatch_brief.py" --run-id <run-id> --write --json
```

3. Spawn only the needed workers.
   - Use one packet per worker from the dispatch brief.
   - Use the packet `prompt` as the subagent message.
   - Prefer the packet `recommended_agent_type` and `recommended_reasoning_effort`.
   - If the packet includes `suggested_workdir`, treat it as the authoritative checkout for that worker's code edits and shell commands.
   - The packet state commands may still point at the main workflow-state repo root via `--repo`; that is intentional.
4. Record the live agent id immediately.
   - After spawn, update the run:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --set-worker-status --worker-id <worker-id> --status running --agent-id <agent-id>
```

5. Keep the conductor moving.
   - Do non-overlapping synthesis setup or additional inspection while workers run.
   - Do not duplicate the worker's bounded assignment.
6. Wait only when blocked.
   - Use `wait_agent` sparingly.
   - Reuse `send_input` only for bounded clarifications.
7. Persist outputs as workers finish.
   - Write each output through `team_state.py --write-output`.
   - If a worker fails or times out, update the run honestly and preserve the partial state.
8. Finalize.
   - Mark the run `synthesizing`
   - let the domain workflow synthesize
   - write the team summary with `team_report.py --write`
   - set the final run status to `completed`, `partial`, `failed`, or `cancelled`
   - close finished workers that are no longer needed

## Runtime Rules

- The conductor owns `.codex-workflows/`.
- Workers must not mutate `.codex-workflows/`.
- Workers should return markdown only.
- Live agent ids belong in repo-local team state as durable execution evidence.
- When execution metadata exists for a worktree-isolated run, the worker should operate in that isolated checkout instead of the main repo checkout.
- If a worker never launched, do not pretend it ran.
- If a worker launched but did not return a usable result, keep that as a real partial/failure signal.

## Suggested Tool Mapping

- `spawn_agent`
  Use to launch the worker from the packet prompt. If the packet includes `suggested_workdir`, preserve that guidance in the worker prompt or any spawn wrapper you use.
- `send_input`
  Use only if the conductor needs to redirect or clarify an already-running worker.
- `wait_agent`
  Use when blocked on a worker result.
- `close_agent`
  Use after the result is persisted and the worker is no longer needed.

## Guardrails

- Do not spawn the whole roster blindly if only one or two workers are still needed.
- Do not overwrite a credible completed worker output unless the conductor has a real reason.
- Do not claim "live team execution" unless actual subagents were spawned.
- Do not let runtime convenience override the domain workflow's evidence standards.
- Do not hide degraded outcomes behind a completed-looking final summary.
