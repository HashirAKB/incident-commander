"""Approval binding rules (approvals.normalize_decisions) as pure unit tests."""

from __future__ import annotations

from typing import Any

import pytest

from incident_commander.actions import args_hash
from incident_commander.approvals import DecisionRefused, normalize_decisions
from incident_commander.nodes.approval import stop_reason_for
from incident_commander.routing import route_after_approval
from incident_commander.state import ApprovalRequest

SCHEMAS: dict[str, dict[str, Any]] = {
    "restart_device": {
        "type": "object",
        "properties": {"device_id": {"type": "string", "pattern": "^D-\\d{2}$"}, "reason": {"type": "string"}},
        "required": ["device_id", "reason"],
    },
    "create_incident": {
        "type": "object",
        "properties": {
            "device_id": {"type": "string"},
            "title": {"type": "string"},
            "severity": {"enum": ["low", "medium", "high", "critical"]},
            "description": {"type": "string"},
        },
        "required": ["device_id", "title", "severity", "description"],
    },
}
RESTART = {"device_id": "D-33", "reason": "radio stuck"}
INCIDENT = {"device_id": "D-33", "title": "Signal loss", "severity": "medium", "description": "x"}
REQUEST: ApprovalRequest = {
    "approval_id": "appr-1",
    "requested_at": "2026-03-01T00:00:00Z",
    "expires_at": "2026-03-01T04:00:00Z",
    "actions": [
        {"action_id": "A1", "tool": "restart_device", "args": RESTART, "rationale": "", "risk": "",
         "args_hash": args_hash("restart_device", RESTART)},
        {"action_id": "A2", "tool": "create_incident", "args": INCIDENT, "rationale": "", "risk": "",
         "args_hash": args_hash("create_incident", INCIDENT)},
    ],
}  # fmt: skip
H1, H2 = REQUEST["actions"][0]["args_hash"], REQUEST["actions"][1]["args_hash"]


def payload(decisions: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {"approval_id": "appr-1", "decided_at": "2026-03-01T01:00:00Z", "decisions": decisions} | overrides


def test_approve_and_reject() -> None:
    result = normalize_decisions(
        REQUEST, payload({"A1": {"verdict": "approve", "args_hash": H1}, "A2": {"verdict": "reject"}}), SCHEMAS
    )
    assert result["A1"]["verdict"] == "approve" and result["A1"]["args"] == RESTART
    assert result["A2"]["verdict"] == "reject" and result["A2"]["args"] is None


def test_missing_decision_means_reject() -> None:
    result = normalize_decisions(REQUEST, payload({"A1": {"verdict": "approve", "args_hash": H1}}), SCHEMAS)
    assert result["A2"]["verdict"] == "reject" and result["A2"]["note"] == "no decision"


def test_stale_approval_id_is_refused() -> None:
    with pytest.raises(DecisionRefused, match="stale"):
        normalize_decisions(REQUEST, payload({}, approval_id="appr-0"), SCHEMAS)


def test_wrong_args_hash_is_refused() -> None:
    with pytest.raises(DecisionRefused, match="args_hash"):
        normalize_decisions(REQUEST, payload({"A1": {"verdict": "approve", "args_hash": H2}}), SCHEMAS)


def test_unknown_action_cannot_be_added() -> None:
    with pytest.raises(DecisionRefused, match="cannot add"):
        normalize_decisions(REQUEST, payload({"A9": {"verdict": "approve", "args_hash": H1}}), SCHEMAS)


def test_decision_after_expiry_is_refused() -> None:
    with pytest.raises(DecisionRefused, match="expired"):
        normalize_decisions(
            REQUEST,
            payload({"A1": {"verdict": "approve", "args_hash": H1}}, decided_at="2026-03-01T04:00:01Z"),
            SCHEMAS,
        )


def test_expiry_payload_expires_every_action() -> None:
    result = normalize_decisions(REQUEST, {"approval_id": "appr-1", "decided_at": "x", "expired": True}, SCHEMAS)
    assert {d["verdict"] for d in result.values()} == {"expired"}
    assert stop_reason_for(result) == "expired"


def test_edit_revalidates_args() -> None:
    edited = INCIDENT | {"severity": "high"}
    result = normalize_decisions(
        REQUEST, payload({"A2": {"verdict": "edit", "args_hash": H2, "args": edited}}), SCHEMAS
    )
    assert result["A2"]["args"] == edited


@pytest.mark.parametrize(
    "bad_edit",
    [
        {"verdict": "edit", "args_hash": H1, "args": {"device_id": "all devices", "reason": "x"}},  # bad pattern
        {"verdict": "edit", "args_hash": H1, "args": RESTART | {"force": True}},  # widens scope: unknown field
        {"verdict": "edit", "args_hash": H1, "args": RESTART, "tool": "create_incident"},  # changes the tool
        {"verdict": "edit", "args_hash": H1, "args": "restart everything"},  # not an object
        {"verdict": "yolo", "args_hash": H1},  # unknown verdict
    ],
)
def test_edits_that_widen_scope_are_refused(bad_edit: dict[str, Any]) -> None:
    with pytest.raises(DecisionRefused):
        normalize_decisions(REQUEST, payload({"A1": bad_edit}), SCHEMAS)


def test_payload_must_be_an_object() -> None:
    with pytest.raises(DecisionRefused):
        normalize_decisions(REQUEST, "approve all", SCHEMAS)


def test_stop_reasons_and_routing_after_approval() -> None:
    rejected = {"A1": {"action_id": "A1", "verdict": "reject", "args": None, "note": ""}}
    approved = {"A1": {"action_id": "A1", "verdict": "approve", "args": RESTART, "note": ""}}
    assert stop_reason_for(rejected) == "rejected"  # type: ignore[arg-type]
    assert stop_reason_for(approved) is None  # type: ignore[arg-type]
    assert route_after_approval({"decisions": rejected}) == "postmortem"  # type: ignore[typeddict-item]
    assert route_after_approval({"decisions": approved}) == "executor"  # type: ignore[typeddict-item]
    assert route_after_approval({}) == "postmortem"  # type: ignore[typeddict-item]
