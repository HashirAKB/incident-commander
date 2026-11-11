# ADR 0005: Each parallel investigator gets an equal share of the remaining budget

Status: accepted (2026-11-10)

## Context

Investigators run in parallel (one `Send` each). A branch cannot see what the others spend while it runs,
so a shared "remaining budget" read inside branches would be stale and would depend on scheduling order.

## Decision

`routing.fan_out_investigators` computes the remaining step, token and cost budget and gives each branch
`remaining // number_of_branches` (`budgets.split`). A branch stops early (finding marked truncated) when it
reaches its share. Totals are merged with a summing reducer and checked again at the `gather` gate.
LangGraph's `recursion_limit` stays as a backstop.

## Consequences

- Deterministic and race-free: the same input always stops at the same point.
- A cheap branch cannot lend its unused share to an expensive one, so a run can stop slightly earlier than
  a shared counter would allow. Tested in `tests/test_graph.py` (each limit yields its own stop_reason).
