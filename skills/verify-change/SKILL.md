---
name: verify-change
description: Run adversarial verification after non-trivial coding work and report evidence-first results. Use when a change touches multiple files, changes behavior, needs a credible done signal, or should end with commands run, observed output, and a PASS, FAIL, or PARTIAL verdict.
---

# Verify Change

## Overview

Use this skill after non-trivial work to verify behavior instead of just reading code and guessing. Reuse existing Codex skills and tools for QA, review, browsing, builds, tests, or audits where they already fit. The output must be evidence-first and must not over-claim.

Your job is to try to break the change, not to reward it for looking plausible.
Verification fails when you stop at the happy path, read code instead of running it,
or let one passing check stand in for real confidence.

## Anti-Avoidance Rules

- Reading code is not verification.
- A passing test suite is supporting evidence, not the whole answer.
- "This is probably fine" is not a verdict.
- If you catch yourself explaining what you would test, stop and run a command instead.
- For non-trivial work, run at least one adversarial probe in addition to the happy path.

## Verification Workflow

1. Inspect the change scope before choosing checks.
   - If the plugin script is available in a repo-local install, run:

```text
python "./plugins/codex-coding-workflows/scripts/analyze_change_scope.py" --json
```

If `python` is unavailable on Windows, use `py -3` instead.

2. If risk level is unclear, run policy review:

```text
py -3 "./plugins/codex-coding-workflows/scripts/policy_check.py" --intent verify --json
```

3. Run the project baseline where it exists.
   - build if the project has a build step
   - run the relevant test suite if it exists
   - run configured linters or type-checkers when they are part of the normal confidence bar
4. Choose direct checks that exercise the real change.
   - frontend: run the app or tests, use browser QA when available, and click the actual changed path
   - backend or API: call endpoints or run integration checks and inspect response shape, not just status codes
   - CLI or scripts: execute representative commands, error paths, and boundary inputs
   - config or infra: run validation or dry-run style checks
   - refactors: prove behavior did not change through existing tests and observable spot checks
   - bug fixes: reproduce the old failure, verify the fix, then probe nearby regressions
5. Reuse existing Codex capabilities where they help.
   - use review or QA workflows when they are the best verification surface
   - do not duplicate an existing built-in skill just for style points
6. Try to break the change.
   - boundary values
   - empty or malformed input
   - repeated runs or idempotency
   - adjacent regression paths
   - concurrency or duplicate-submission behavior when applicable
7. Persist the evidence.
   - Append a verification entry to `.codex-workflows/verification-log.jsonl` through `memory_sync.py` using `--append-verification-json` or `--append-verification-file`.
8. If verification state is invalid, repair the log before trusting it.
   - Use `workflow-state-repair` when malformed verification entries block a credible verdict.
9. Generate a reviewer-facing artifact when needed.
   - If the user wants a clean recap after verification, route to `review-ready-summary`.

## Required Check Format

Every meaningful check must use this structure:

```text
### Check: [what you verified]
**Command run:**
  [exact command]
**Output observed:**
  [real output or the important excerpt]
**Result: PASS**
```

If the check failed, use `**Result: FAIL**` and include expected versus actual behavior.

Checks without commands or observed output are not PASS checks. They are skips.

## Required Output Format

For every meaningful check, include:

- what you verified
- the exact command you ran
- the observed output or the important excerpt
- the result as PASS or FAIL with a short explanation

End with exactly one verdict line:

```text
VERDICT: PASS
```

or

```text
VERDICT: FAIL
```

or

```text
VERDICT: PARTIAL
```

Use PARTIAL only when the environment prevented a meaningful check. Do not use it to hide uncertainty.

## Guardrails

- Do not claim success without commands and observed output.
- Do not reduce verification to code reading or intuition.
- Do not stop at the first polished-looking success signal.
- Do not suppress failing checks just to produce a green closeout.
- Do not skip verification logging for medium/high-risk changes that you actually checked.
- If you could not verify something important, say so plainly.
