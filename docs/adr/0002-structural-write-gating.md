# ADR 0002: Write tools are gated by structure, not by prompts

Status: accepted (2026-11-10)

## Context

A prompt such as "never restart a device without approval" can be ignored by the model or overridden by
injected text in a runbook. The telemetry-ops-copilot loop enforced approval in code, but the model still
saw and could call the write tools.

## Decision

- `registry.py` classifies every MCP tool as READ or WRITE from `docs/mcp-contract.md`; unknown tools
  stop the run from starting.
- WRITE tools are passed to exactly one node, the executor (`graph.py` -> `make_executor`). No model is
  ever bound to them. The synthesizer only receives their argument schemas, as data.
- The executor runs an action only if the approval node stored an `approve` or `edit` decision for it,
  bound to the approval id and the args hash (`approvals.py`).

## Consequences

- A hijacked model can only propose actions; proposals are validated and shown to a human.
- `tests/test_structural_safety.py` proves it three ways: a walk over every node's reachable objects, a spy
  model that records every tool set ever bound, and a grep over `src/`.
- Investigators that ask for a write tool get a "refused" tool message, which is recorded in the finding.
