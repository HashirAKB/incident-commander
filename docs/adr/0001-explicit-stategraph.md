# ADR 0001: Explicit StateGraph instead of a prebuilt agent

Status: accepted (2026-11-10)

## Context

LangGraph ships `create_react_agent`, a ready-made model-and-tools loop. It would run this incident flow in
about ten lines. The flow, though, has fixed stages (triage, parallel investigation, synthesis, approval,
execution, postmortem) with different tools and different safety rules per stage.

## Decision

Build the graph explicitly with `StateGraph` in `src/incident_commander/graph.py`: every node, edge and
conditional route is visible in one file, routing functions live in `routing.py` and are unit tested. The
investigator is a small explicit subgraph (`nodes/investigator.py`), not a prebuilt agent. The prebuilt
agent appears only in `evals/baseline.py`, as the comparison.

## Consequences

- Each stage gets only the tools it needs; write tools can be held by one node (ADR 0002).
- More code than the prebuilt agent (see COMPARISON.md), but every decision has a named function and a test.
- The eval shows the cost of the alternative: the baseline re-sends its whole history every turn (about
  2.8x the graph's mock tokens over the 39 cases, see evals/results/mock.md) and lets the model hold
  write tools.
