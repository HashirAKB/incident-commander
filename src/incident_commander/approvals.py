"""Turn an approver's answer into per-action decisions, or refuse it.

The same function runs twice: in the runner/API before resuming (a refusal is
an HTTP 409 / CLI error) and inside the approval node after resuming (defense
in depth: a resume payload that skipped the runner is checked anyway).

A decision is bound to:
- thread_id      (it is resumed on that thread only)
- approval_id    (must match the pending request, so stale answers fail)
- action_id      (only actions in the request; no new actions)
- args_hash      (the approver saw exactly these args)
and arrives before expires_at. Approvers may edit args, but edited args are
validated with the same schema and the tool cannot change.
"""

from __future__ import annotations

from typing import Any

from incident_commander.actions import InvalidAction, validate_write_args
from incident_commander.state import ApprovalRequest, Decision
from incident_commander.timeutil import parse_iso

VERDICTS_FROM_APPROVER = ("approve", "edit", "reject")


class DecisionRefused(ValueError):
    """The answer cannot be applied (stale, expired, tampered or malformed)."""


def expired_decisions(request: ApprovalRequest, note: str) -> dict[str, Decision]:
    return {
        a["action_id"]: {"action_id": a["action_id"], "verdict": "expired", "args": None, "note": note}
        for a in request["actions"]
    }


def normalize_decisions(
    request: ApprovalRequest, payload: Any, write_schemas: dict[str, dict[str, Any]]
) -> dict[str, Decision]:
    """Validate a resume payload against the pending request. Raises DecisionRefused."""
    if not isinstance(payload, dict):
        raise DecisionRefused("decision payload must be an object")
    if payload.get("approval_id") != request["approval_id"]:
        raise DecisionRefused("stale or unknown approval_id: this run is waiting for a different approval")
    decided_at = payload.get("decided_at")
    if not isinstance(decided_at, str):
        raise DecisionRefused("decided_at missing")
    if payload.get("expired") is True:
        return expired_decisions(request, "approval expired before anyone answered")
    if parse_iso(decided_at) > parse_iso(request["expires_at"]):
        raise DecisionRefused(f"approval expired at {request['expires_at']}")

    answers = payload.get("decisions")
    if not isinstance(answers, dict):
        raise DecisionRefused("decisions must be an object keyed by action_id")
    pending = {a["action_id"]: a for a in request["actions"]}
    unknown = sorted(set(answers) - set(pending))
    if unknown:
        raise DecisionRefused(f"unknown action ids {unknown}: approvers cannot add actions")

    decisions: dict[str, Decision] = {}
    for action_id, action in pending.items():
        answer = answers.get(action_id)
        if answer is None:
            # Missing decision = reject. Silence never approves anything.
            decisions[action_id] = {"action_id": action_id, "verdict": "reject", "args": None, "note": "no decision"}
            continue
        if not isinstance(answer, dict) or answer.get("verdict") not in VERDICTS_FROM_APPROVER:
            raise DecisionRefused(f"{action_id}: verdict must be one of {VERDICTS_FROM_APPROVER}")
        verdict = answer["verdict"]
        note = str(answer.get("note", ""))[:500]
        if verdict == "reject":
            decisions[action_id] = {"action_id": action_id, "verdict": "reject", "args": None, "note": note}
            continue
        if answer.get("args_hash") != action["args_hash"]:
            raise DecisionRefused(f"{action_id}: args_hash does not match the pending action (stale view?)")
        if "tool" in answer and answer["tool"] != action["tool"]:
            raise DecisionRefused(f"{action_id}: the tool of an action cannot be changed")
        if verdict == "approve":
            args = action["args"]
        else:
            try:
                args = validate_write_args(action["tool"], answer.get("args"), write_schemas)
            except InvalidAction as exc:
                raise DecisionRefused(f"{action_id}: edited args rejected: {exc}") from exc
        decisions[action_id] = {"action_id": action_id, "verdict": verdict, "args": dict(args), "note": note}
    return decisions
