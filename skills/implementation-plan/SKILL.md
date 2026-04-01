---
name: implementation-plan
description: Create a decision-complete implementation plan for ambiguous or non-trivial coding work. Use when the user wants a plan before coding, when multiple valid approaches exist, or when success criteria, scope, constraints, and tests need to be locked before execution.
---

# Implementation Plan

## Overview

Turn a coding request into a decision-complete implementation plan. Explore the actual repo first, then produce a plan that covers goal, success criteria, scope boundaries, constraints, implementation approach, public interface changes, edge cases, and explicit test cases.

This is a planning-only skill. Stay read-only while it is active. Do not start implementing, do not write files, and do not quietly turn unresolved design choices into implementation work.

## Planning Stance

- Read the repo before planning.
- Resolve discoverable facts from code, config, and existing patterns instead of pushing that work back to the user.
- Treat missing high-impact decisions as planning failures, not details to leave for later.
- If the task is trivial and the implementation is obvious, say so explicitly and keep the plan short.

## Workflow

1. Ground in the repo first.
   - Read the most relevant files, entrypoints, configs, types, and existing patterns before asking clarifying questions.
   - Resolve discoverable facts from the environment instead of pushing that work back to the user.
2. Lock intent.
   - State the user goal, success criteria, audience, in-scope work, out-of-scope work, constraints, and any meaningful tradeoffs.
   - Ask only the questions that materially change the implementation.
3. Lock implementation decisions.
   - Choose the approach, interfaces, data flow, validation rules, migration or compatibility behavior, and failure modes.
   - Leave no important decisions to the implementer.
   - Name important defaults and assumptions instead of letting them stay implicit.
4. Seed workflow state when helpful.
   - If the task is clearly multi-step, prepare or refresh `.codex-workflows/active-task-loop.md`.
   - Record only durable constraints or decisions in `.codex-workflows/memory.md`.
5. End with an implementation-ready plan.
   - Include a concise summary.
   - Include important interface or type changes.
   - Include explicit tests and acceptance scenarios.
   - Include assumptions and defaults chosen where needed.
   - End with the 3-5 files most critical to implementation when the task is non-trivial.

## Output Requirements

Produce a plan that is concise by default but decision complete. Make sure it includes:

- summary of the intended outcome
- success criteria and scope boundaries
- key implementation changes grouped by behavior or subsystem
- important interface, type, data-flow, or compatibility changes
- edge cases and failure modes
- test cases and acceptance checks
- workflow-state updates when `.codex-workflows/` should change
- assumptions, defaults, and critical files for implementation

If the user already provided a plan, treat this skill as a refinement pass: close gaps, remove ambiguity, and make the plan implementable without further design decisions.

## Guardrails

- Do not slip from planning into implementation.
- Do not leave major design decisions unresolved just to stay concise.
- Do not substitute vague guidance for concrete decisions.
- Do not invent runtime features Codex does not have.
- Do not turn a request for implementation into open-ended brainstorming.
- Do not write speculative notes into repo-local memory.
