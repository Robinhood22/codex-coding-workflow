# Live Subagent Protocol

Use this protocol when the current Codex environment supports real subagent tools and the workflow has already decided that team mode is warranted.

## Conductor Loop

1. Initialize the run and worker assignments.
2. Build one worker packet per worker:

```text
python3 "./scripts/team_worker_packet.py" --run-id <run-id> --worker-id <worker-id> --json
```

3. Spawn the worker with:
   - `agent_type`: usually `default`
   - `reasoning_effort`: use the packet recommendation unless the workflow has a stronger reason
   - `message`: the packet `prompt`
4. Record the live agent id immediately:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --set-worker-status --worker-id <worker-id> --status running --agent-id <agent-id>
```

5. Continue conductor work while workers run.
6. Wait only when blocked on worker results.
7. Persist each worker output as it completes:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --write-output --worker-id <worker-id> --output-file <worker-output-file> --summary "<one-line summary>" --confidence <0.0-1.0>
```

8. Mark the run `synthesizing`, write the summary, then close the run.

## Worker Boundaries

- Workers are not allowed to mutate `.codex-workflows/`.
- Workers should stay within their assigned responsibility.
- Workers should return markdown only; the conductor owns synthesis.
- Workers should not claim consensus, proof, or reviewer signoff.

## Degraded Mode

If live subagents are unavailable:

- keep the same run state and assignments
- execute the worker roles serially in the conductor thread
- mark the run `partial` if the missing parallel execution materially lowers confidence
