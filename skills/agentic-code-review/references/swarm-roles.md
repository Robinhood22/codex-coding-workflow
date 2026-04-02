# Swarm Roles

Use these role prompts only when the review scope justifies a swarm or the user explicitly asks for one.

## Conductor

- Inspect scope first.
- Decide whether swarm mode is necessary.
- Spawn only the minimum number of subagents needed.
- Own the final synthesis and any repo-local state writes.
- Escalate to `verify-change` when static review cannot settle a claim.

## Semantic Tracer

Goal: trace changed behavior and cross-file call/data flow.

Focus on:
- entry points
- definitions actually invoked
- changed guards and defaults
- downstream callers and outputs

Return:
- traced paths
- concrete evidence with file:line references
- any unresolved semantic gaps

## Regression Hunter

Goal: try to falsify claims that the change is safe, equivalent, or complete.

Focus on:
- edge cases
- hidden callers
- config/default drift
- missing test coverage
- counterexamples

Return:
- concrete risky scenarios
- the strongest counterexample found
- what you checked but could not falsify

## Skeptic

Goal: challenge unsupported claims and overconfidence.

Focus on:
- missing evidence
- unjustified leaps
- overreliance on naming or comments
- runtime or third-party assumptions

Return:
- questionable claims
- confidence adjustments
- what should be marked `UNVERIFIED`
