"""The whole incident graph, wired explicitly in one place.

    START -> triage -(Send x N)-> investigate -> gather -> synthesizer
          -> prepare_approval -> approval (interrupt) -> executor -> postmortem -> END

Budget gates after triage, gather and synthesizer can short-cut to
budget_stop -> postmortem. Read routing.py for every conditional edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from incident_commander.ledger import Ledger
from incident_commander.nodes.approval import make_approval, make_prepare_approval
from incident_commander.nodes.budget import budget_stop, gather
from incident_commander.nodes.executor import make_executor
from incident_commander.nodes.investigator import build_investigator
from incident_commander.nodes.postmortem import make_postmortem
from incident_commander.nodes.synthesizer import make_synthesizer
from incident_commander.nodes.triage import make_triage
from incident_commander.registry import Toolbox
from incident_commander.routing import (
    fan_out_investigators,
    route_after_approval,
    route_after_gather,
    route_after_synthesizer,
)
from incident_commander.state import IncidentState
from incident_commander.timeutil import Clock

RECURSION_LIMIT = 60  # backstop only: the budgets stop a run long before this


@dataclass(frozen=True)
class GraphDeps:
    model: BaseChatModel
    toolbox: Toolbox
    ledger: Ledger
    runs_dir: Path
    clock: Clock


def write_schemas(toolbox: Toolbox) -> dict[str, dict[str, Any]]:
    """Argument schemas of the WRITE tools: data the synthesizer may see, not callables."""
    return {name: dict(tool.args_schema) for name, tool in toolbox.write_tools.items()}  # type: ignore[arg-type]


def build_graph(
    deps: GraphDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(IncidentState)

    graph.add_node("triage", make_triage(deps.model))
    graph.add_node("investigate", build_investigator(deps.model, deps.toolbox.read_tools))
    graph.add_node("gather", gather)
    graph.add_node("synthesizer", make_synthesizer(deps.model, write_schemas(deps.toolbox)))
    graph.add_node("prepare_approval", make_prepare_approval(deps.clock))
    graph.add_node("approval", make_approval(write_schemas(deps.toolbox)))
    # The one and only place WRITE tools enter the graph.
    graph.add_node("executor", make_executor(deps.toolbox.write_tools, deps.ledger))
    graph.add_node("budget_stop", budget_stop)
    graph.add_node("postmortem", make_postmortem(deps.runs_dir))

    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", fan_out_investigators, ["investigate", "budget_stop"])
    graph.add_edge("investigate", "gather")
    graph.add_conditional_edges("gather", route_after_gather, ["synthesizer", "budget_stop"])
    graph.add_conditional_edges(
        "synthesizer", route_after_synthesizer, ["prepare_approval", "postmortem", "budget_stop"]
    )
    graph.add_edge("prepare_approval", "approval")
    graph.add_conditional_edges("approval", route_after_approval, ["executor", "postmortem"])
    graph.add_edge("executor", "postmortem")
    graph.add_edge("budget_stop", "postmortem")
    graph.add_edge("postmortem", END)

    return graph.compile(checkpointer=checkpointer)
