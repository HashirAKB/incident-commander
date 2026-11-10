# Plan

Resume from this file alone: read "In progress" and "Next", then the milestone table.

## Milestones

| id | status | started (date -u) | finished (date -u) | verified by |
| --- | --- | --- | --- | --- |
| M0 spec and skeleton | in progress | 2026-11-10 20:08 UTC | | ruff, mypy, pytest |
| M1 MCP bridge | todo | | | tests/test_mcp_contract.py |
| M2 models and budgets | todo | | | tests/test_models.py tests/test_budgets.py |
| M3 graph without writes | todo | | | ic run + tests/test_graph.py |
| M4 approval and durability | todo | | | tests/test_crash.py tests/test_structural_safety.py |
| M5 time travel and tracing | todo | | | tests/test_timetravel.py tests/test_tracing.py |
| M6 API and approval page | todo | | | tests/test_api.py |
| M7 evals | todo | | | python -m evals.run --mode mock |
| M8 comparison, packaging, docs | todo | | | scripts/check_dod.py |
| Final reviewer pass | todo | | | all of the above |

## In progress

M0.

## Next

M1: servers/seed.py, servers/telemetry_stub, servers/fleet_context, registry, docs/mcp-contract.md.

## Blockers

- (resolved 2026-11-10) GitHub repo did not exist and could not be created from the session; owner created it.
