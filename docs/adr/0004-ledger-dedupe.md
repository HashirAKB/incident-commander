# ADR 0004: Ledger dedupe (at-least-once) instead of exactly-once writes

Status: accepted (2026-11-10)

## Context

The executor calls an MCP write tool, then the graph checkpoints. If the process dies between the two, a
resume re-runs the executor and could restart the device again. Exactly-once delivery would need the
device API to accept an idempotency key or to take part in a transaction; the telemetry server does neither.

## Decision

A write ledger (`ledger.py`, SQLite or Postgres) keyed by `(thread_id, action_id, args_hash)`. The executor
checks it before each write and records the result right after the tool returns. Replays, forks and
resumes skip any action already in the ledger. The args hash is in the key so that a different action
that reuses an id after a fork is not wrongly skipped.

## Consequences

- Crash after the ledger row and before the checkpoint: no duplicate (tested by replaying the executor).
- Remaining window: crash after the server performed the write and before the ledger insert commits. The
  action runs again on resume. This is at-least-once with dedupe, stated in README and SECURITY.md.
- Closing the window needs an idempotency key on the device API (listed under "what I'd do next").
