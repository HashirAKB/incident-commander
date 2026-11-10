"""prepare_approval and approval: the human-in-the-loop pause.

Why two nodes? LangGraph resumes an interrupted node by running it again from
the top; interrupt() then returns the resume value instead of pausing. Any
code before interrupt() therefore runs twice: once before the pause and once
on resume, possibly hours later in another process. If the deadline were
computed there, `now() + ttl` on resume would move the deadline (a run could
never expire), and a fresh uuid would no longer match the id the approver saw.

So prepare_approval computes everything non-deterministic (approval_id,
requested_at, expires_at, arg hashes) and the checkpointer saves it. The
approval node only reads state, calls interrupt(), and validates the answer.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from langgraph.types import interrupt

from incident_commander import tracing
from incident_commander.actions import args_hash
from incident_commander.approvals import DecisionRefused, normalize_decisions
from incident_commander.nodes import StateNode
from incident_commander.state import ApprovalRequest, Decision, IncidentState, PendingAction, StopReason
from incident_commander.timeutil import Clock, to_iso


def make_prepare_approval(clock: Clock) -> StateNode:
    async def prepare_approval(state: IncidentState) -> dict[str, Any]:
        with tracing.node_span("prepare_approval"):
            now = clock()
            actions: list[PendingAction] = [
                {**action, "args_hash": args_hash(action["tool"], action["args"])}
                for action in state.get("proposed_actions", [])
            ]
            request: ApprovalRequest = {
                "approval_id": str(uuid.uuid4()),
                "requested_at": to_iso(now),
                "expires_at": to_iso(now + timedelta(seconds=state["approval_ttl_seconds"])),
                "actions": actions,
            }
            return {"approval_request": request}

    return prepare_approval


def stop_reason_for(decisions: dict[str, Decision]) -> StopReason | None:
    verdicts = {d["verdict"] for d in decisions.values()}
    if verdicts & {"approve", "edit"}:
        return None  # something will run; postmortem decides "completed"
    return "expired" if verdicts == {"expired"} else "rejected"


def make_approval(write_schemas: dict[str, dict[str, Any]]) -> StateNode:
    async def approval(state: IncidentState) -> dict[str, Any]:
        request = state["approval_request"]
        # Nothing above this line may have side effects or read the clock: on
        # resume, this function runs again from the top.
        payload = interrupt(request)
        with tracing.node_span("approval"):
            try:
                decisions = normalize_decisions(request, payload, write_schemas)
            except DecisionRefused as exc:
                # The runner refuses bad answers before resuming; reaching here means
                # someone resumed the graph directly. Treat it as a rejection of everything.
                decisions = {
                    a["action_id"]: {
                        "action_id": a["action_id"],
                        "verdict": "reject",
                        "args": None,
                        "note": f"refused: {exc}",
                    }
                    for a in request["actions"]
                }
            update: dict[str, Any] = {"decisions": decisions}
            reason = stop_reason_for(decisions)
            if reason:
                update["stop_reason"] = reason
            return update

    return approval
