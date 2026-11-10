"""Conditional edges: pure functions from state to the next node(s).

Kept separate from graph.py so each decision can be unit tested without
building a graph or calling a model.
"""

from __future__ import annotations

from typing import Literal

from langgraph.types import Send

from incident_commander import budgets
from incident_commander.nodes.budget import over_budget
from incident_commander.state import IncidentState, InvestigatorInput


def fan_out_investigators(state: IncidentState) -> list[Send] | Literal["budget_stop"]:
    """One Send per chosen investigator, each with an equal share of the remaining budget.

    Send runs the same "investigate" node once per item, in parallel, each with
    its own input. Branches cannot see each other's spend, hence the split.
    """
    if over_budget(state):
        return "budget_stop"
    names = state["triage"]["investigators"]
    share = budgets.split(budgets.remaining(state["usage"], state["limits"]), len(names))
    return [
        Send(
            "investigate",
            InvestigatorInput(investigator=name, alert=state["alert"], triage=state["triage"], branch_budget=share),
        )
        for name in names
    ]


def route_after_gather(state: IncidentState) -> Literal["synthesizer", "budget_stop"]:
    return "budget_stop" if over_budget(state) else "synthesizer"


def route_after_synthesizer(state: IncidentState) -> Literal["prepare_approval", "postmortem", "budget_stop"]:
    if over_budget(state):
        return "budget_stop"
    return "prepare_approval" if state.get("proposed_actions") else "postmortem"


def route_after_approval(state: IncidentState) -> Literal["executor", "postmortem"]:
    """Only an explicit approve or edit leads to the executor. Missing = reject."""
    decisions = state.get("decisions", {})
    approved = any(d["verdict"] in ("approve", "edit") for d in decisions.values())
    return "executor" if approved else "postmortem"
