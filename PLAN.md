# Plan

Resume from this file alone: read "In progress" and "Next", then the milestone table.

## Milestones

| id | status | started (date -u) | finished (date -u) | verified by |
| --- | --- | --- | --- | --- |
| M0 spec and skeleton | done | 2026-11-10 20:08 UTC | 2026-11-10 20:15 UTC | ruff, mypy, pytest |
| M1 MCP bridge | done | 2026-11-10 20:16 UTC | 2026-11-10 20:20 UTC | tests/test_mcp_contract.py |
| M2 models and budgets | done | 2026-11-10 20:21 UTC | 2026-11-10 20:25 UTC | tests/test_models.py tests/test_budgets.py |
| M3 graph without writes | done | 2026-11-10 20:27 UTC | 2026-11-10 20:31 UTC | ic run + tests/test_graph.py |
| M4 approval and durability | todo | | | tests/test_crash.py tests/test_structural_safety.py |
| M5 time travel and tracing | todo | | | tests/test_timetravel.py tests/test_tracing.py |
| M6 API and approval page | todo | | | tests/test_api.py |
| M7 evals | todo | | | python -m evals.run --mode mock |
| M8 comparison, packaging, docs | todo | | | scripts/check_dod.py |
| Final reviewer pass | todo | | | all of the above |

## In progress

M4.

## Next

M4: prepare_approval, approval (interrupt), decision validation, ledger + executor, sweeper, CLI approve/pending/sweep-expired, crash tests a+b, structural safety test, Postgres CI job.

## Blockers

- (resolved 2026-11-10) GitHub repo did not exist and could not be created from the session; owner created it.
