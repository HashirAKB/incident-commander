"""synthesizer: findings -> diagnosis + proposed WRITE actions (not executed).

The model proposes; this node validates. Anything that is not a well-formed
call to a WRITE tool from the contract is dropped and listed in
`dropped_proposals`, so the postmortem shows what the model tried.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models import BaseChatModel

from incident_commander import prompts, tracing
from incident_commander.actions import MAX_ACTIONS, InvalidAction, validate_write_args
from incident_commander.llm import context_messages, structured_call
from incident_commander.models.schemas import ActionProposal, SynthesisOutput
from incident_commander.nodes import StateNode
from incident_commander.state import Diagnosis, IncidentState, ProposedAction


def validate_proposals(
    proposals: list[ActionProposal], write_schemas: dict[str, dict[str, Any]]
) -> tuple[list[ProposedAction], list[str]]:
    accepted: list[ProposedAction] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for proposal in proposals:
        try:
            args = validate_write_args(proposal.tool, json.loads(proposal.args_json), write_schemas)
        except (InvalidAction, json.JSONDecodeError) as exc:
            dropped.append(f"{proposal.tool}: {exc}")
            continue
        key = json.dumps([proposal.tool, args], sort_keys=True)
        if key in seen:
            continue
        if len(accepted) >= MAX_ACTIONS:
            dropped.append(f"{proposal.tool}: over the limit of {MAX_ACTIONS} actions per incident")
            continue
        seen.add(key)
        # The node assigns action ids, never the model: ids must be stable and unique.
        accepted.append(
            {
                "action_id": f"A{len(accepted) + 1}",
                "tool": proposal.tool,
                "args": args,
                "rationale": proposal.rationale,
                "risk": proposal.risk,
            }
        )
    return accepted, dropped


def make_synthesizer(model: BaseChatModel, write_schemas: dict[str, dict[str, Any]]) -> StateNode:
    """Receives the WRITE tools' argument schemas (plain dicts), never the tools."""

    async def synthesizer(state: IncidentState) -> dict[str, Any]:
        with tracing.node_span("synthesizer"):
            findings = state["findings"]
            context = {
                "alert": state["alert"],
                "triage": state["triage"],
                "findings": {
                    name: {"finding_id": f["finding_id"], "summary": f["summary"]} for name, f in findings.items()
                },
            }
            messages = context_messages(prompts.SYNTHESIZER, context)
            output, usage = await structured_call(model, SynthesisOutput, messages, node="synthesizer")
            known_ids = {f["finding_id"] for f in findings.values()}
            diagnosis: Diagnosis = {
                "root_cause": output.root_cause,
                "confidence": output.confidence,
                "summary": output.summary,
                "evidence": [e for e in output.evidence if e in known_ids],
            }
            actions, dropped = validate_proposals(output.actions, write_schemas)
            return {"diagnosis": diagnosis, "proposed_actions": actions, "dropped_proposals": dropped, "usage": usage}

    return synthesizer
