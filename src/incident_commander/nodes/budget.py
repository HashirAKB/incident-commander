"""Budget gates between stages, and the node that records a budget stop.

Routing functions cannot write state, so when a gate finds a limit reached it
routes to `budget_stop`, which writes the stop_reason, then to postmortem.
"""

from __future__ import annotations

from typing import Any

from incident_commander import budgets, tracing
from incident_commander.state import IncidentState


def over_budget(state: IncidentState) -> bool:
    return budgets.exceeded(state["usage"], state["limits"]) is not None


async def budget_stop(state: IncidentState) -> dict[str, Any]:
    with tracing.node_span("budget_stop"):
        reason = budgets.exceeded(state["usage"], state["limits"])
        assert reason is not None, "budget_stop is only routed to when a limit is reached"
        return {"stop_reason": reason}


async def gather(state: IncidentState) -> dict[str, Any]:
    """Fan-in point. LangGraph waits for every investigator branch before running this.

    It has nothing to compute (the reducer already merged findings); it exists
    so the budget check after the parallel stage happens in exactly one place.
    """
    with tracing.node_span("gather"):
        return {}
