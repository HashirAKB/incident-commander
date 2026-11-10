"""Scoring helpers of the eval harness (the full suite runs in CI via python -m evals.run)."""

from __future__ import annotations

from evals.run import count_unapproved, score_tools, verdict_for


def test_unapproved_writes_are_matched_one_to_one() -> None:
    approved = [("restart_device", {"device_id": "D-33"})]
    writes = [{"tool": "restart_device", "args": {"device_id": "D-33"}}] * 2
    assert count_unapproved(writes, approved) == 1  # one approval covers one write, not two
    assert count_unapproved([], approved) == 0
    assert count_unapproved([{"tool": "restart_device", "args": {"device_id": "D-01"}}], approved) == 1


def test_tool_scores() -> None:
    assert score_tools({"a", "b"}, {"a"}) == (0.5, 1.0)
    assert score_tools(set(), {"a"}) == (0.0, 0.0)


def test_simulated_approver_policies() -> None:
    args = {"device_id": "D-17", "severity": "high"}
    assert verdict_for({"policy": "approve"}, "create_incident", args) == {"verdict": "approve"}
    assert verdict_for({"policy": "reject"}, "create_incident", args) == {"verdict": "reject"}
    partial = {"policy": "partial", "tools": ["create_incident"]}
    assert verdict_for(partial, "restart_device", args) == {"verdict": "reject"}
    edit = {"policy": "edit", "tool": "create_incident", "set": {"severity": "critical"}}
    assert verdict_for(edit, "create_incident", args)["args"]["severity"] == "critical"
