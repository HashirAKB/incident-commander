"""triage: classify the alert and choose which investigators to run."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from incident_commander import prompts, tracing
from incident_commander.llm import context_messages, structured_call
from incident_commander.models.schemas import TriageOutput
from incident_commander.nodes import StateNode
from incident_commander.state import INVESTIGATORS, IncidentState, InvestigatorName, Triage


def choose_investigators(requested: list[InvestigatorName], target: str) -> list[InvestigatorName]:
    """metrics always runs; events needs a single device; order is fixed for determinism."""
    chosen = set(requested) | {"metrics"}
    if target == "fleet":
        chosen.discard("events")
    return [name for name in INVESTIGATORS if name in chosen]


def make_triage(model: BaseChatModel) -> StateNode:
    async def triage(state: IncidentState) -> dict[str, Any]:
        with tracing.node_span("triage"):
            alert = state["alert"]
            messages = context_messages(prompts.TRIAGE, {"alert": alert})
            output, usage = await structured_call(model, TriageOutput, messages, node="triage")
            result: Triage = {
                "category": output.category,
                "severity": output.severity,
                "investigators": choose_investigators(output.investigators, alert["target"]),
                "rationale": output.rationale,
            }
            return {"triage": result, "usage": usage}

    return triage
