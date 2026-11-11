# ADR 0003: Approval deadline is computed in a node before the interrupt

Status: accepted (2026-11-10)

## Context

When a run resumes after `interrupt()`, LangGraph re-runs the interrupted node from its first line;
`interrupt()` then returns the resume value. Code before `interrupt()` therefore runs twice, the second
time possibly hours later in another process. Computing `expires_at = now() + ttl` or an approval id there
would move the deadline on every resume and break the id the approver saw.

## Decision

A separate `prepare_approval` node computes `approval_id`, `requested_at`, `expires_at` and the args hash
of each action. Its output is checkpointed. The `approval` node only reads state, calls `interrupt()`, and
validates the answer. Nodes never read the clock; the runner and sweeper take an injectable clock and pass
`decided_at` in the resume payload.

## Consequences

- Deadlines survive restarts and cannot drift. Expiry is testable by moving an injected clock.
- One extra node and checkpoint per run.
