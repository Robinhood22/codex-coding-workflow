# Live Loop

Use this when a team run already exists and the current Codex runtime supports live
subagent tools.

## Conductor Sequence

1. Inspect the current run.

```text
python3 "./scripts/team_state.py" --run-id <run-id> --show-run --json
```

2. Build the dispatch brief.

```text
python3 "./scripts/team_dispatch_brief.py" --run-id <run-id> --write --json
```

3. For each selected worker:
   - read the packet prompt
   - if the packet includes `suggested_workdir`, treat that path as the authoritative checkout for the worker's code edits and shell commands
   - call `spawn_agent`
   - record the returned agent id with the packet status update command

4. Keep doing non-overlapping conductor work.

5. When blocked, wait on the active worker ids.

6. When a worker returns:
   - persist the worker output through `team_state.py --write-output`
   - decide whether the worker should remain open
   - close it if it is finished and no longer needed

7. Finalize the run:

```text
python3 "./scripts/team_state.py" --run-id <run-id> --set-run-status --status synthesizing --reason "Collecting live worker outputs for synthesis"
python3 "./scripts/team_report.py" --run-id <run-id> --write
python3 "./scripts/team_state.py" --run-id <run-id> --set-run-status --status <completed|partial|failed|cancelled>
```

## Failure Handling

- If spawn fails, leave the worker `pending` or mark it `failed` with a reason.
- If the worker returns an unusable result, persist what happened honestly and downgrade the run if needed.
- If live tools disappear mid-run, keep the state and degrade to serial work instead of abandoning the run.
