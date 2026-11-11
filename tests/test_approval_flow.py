"""Approval end to end: real stdio MCP servers (stub), fake model, injected clock.

Writes are counted from the MCP server's own write log, not from our state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from incident_commander.approvals import DecisionRefused
from incident_commander.config import Settings
from incident_commander.runner import (
    RunStatus,
    Runtime,
    config_for,
    drive,
    expire_run,
    list_pending,
    open_runtime,
    start_run,
    submit_decisions,
    sweep_expired,
)
from tests.conftest import MutableClock, alert, write_log_lines

BIG = {"max_steps": 100, "max_tokens": 1_000_000, "max_cost_usd": 100.0}


async def paused_signal_run(rt: Runtime) -> RunStatus:
    status = await start_run(rt, alert("signal_d33"), limits=BIG)  # type: ignore[arg-type]
    assert status.phase == "paused", status.phase
    return status


def answers_for(status: RunStatus, verdicts: dict[str, str]) -> dict[str, Any]:
    request = status.approval_request
    assert request is not None
    hashes = {a["action_id"]: a["args_hash"] for a in request["actions"]}
    return {aid: {"verdict": v, "args_hash": hashes[aid]} for aid, v in verdicts.items()}


async def test_pause_then_approve_all_executes_each_action_once(settings: Settings, backend: str) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        paused = await paused_signal_run(rt)
        request = paused.approval_request
        assert request is not None
        assert [a["tool"] for a in request["actions"]] == ["restart_device", "create_incident"]
        assert write_log_lines(settings) == []  # nothing ran before approval

        done = await submit_decisions(
            rt, paused.thread_id, request["approval_id"], answers_for(paused, {"A1": "approve", "A2": "approve"})
        )
    assert done.phase == "finished" and done.stop_reason == "completed"
    assert [e["status"] for e in done.values["executions"]] == ["executed", "executed"]
    assert [json.loads(line)["tool"] for line in write_log_lines(settings)] == ["restart_device", "create_incident"]
    assert "executed" in Path(done.values["postmortem_path"]).read_text()


async def test_reject_all_means_no_writes(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        paused = await paused_signal_run(rt)
        done = await submit_decisions(
            rt,
            paused.thread_id,
            paused.approval_request["approval_id"],
            {"A1": {"verdict": "reject"}},  # type: ignore[index]
        )
    assert done.stop_reason == "rejected"
    assert {d["verdict"] for d in done.values["decisions"].values()} == {"reject"}  # A2 missing -> reject
    assert write_log_lines(settings) == []


async def test_edit_then_approve_runs_the_edited_args(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        paused = await paused_signal_run(rt)
        request = paused.approval_request
        assert request is not None
        incident = request["actions"][1]
        edited = incident["args"] | {"severity": "critical"}
        answers = {"A2": {"verdict": "edit", "args_hash": incident["args_hash"], "args": edited}}
        done = await submit_decisions(rt, paused.thread_id, request["approval_id"], answers)
    assert done.stop_reason == "completed"
    writes = [json.loads(line) for line in write_log_lines(settings)]
    assert writes == [{"tool": "create_incident", "args": edited}]  # A1 had no decision: rejected


async def test_refusals_leave_the_run_paused(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        paused = await paused_signal_run(rt)
        approval_id = paused.approval_request["approval_id"]  # type: ignore[index]
        with pytest.raises(DecisionRefused, match="stale"):
            await submit_decisions(rt, paused.thread_id, "not-the-id", answers_for(paused, {"A1": "approve"}))
        with pytest.raises(DecisionRefused, match="tool"):
            bad = answers_for(paused, {"A1": "edit"})
            bad["A1"] |= {"tool": "create_incident", "args": {}}
            await submit_decisions(rt, paused.thread_id, approval_id, bad)
        with pytest.raises(DecisionRefused, match="cannot add"):
            await submit_decisions(rt, paused.thread_id, approval_id, {"A3": {"verdict": "approve"}})
        assert [s.thread_id for s in await list_pending(rt)] == [paused.thread_id]
    assert write_log_lines(settings) == []


async def test_expiry_via_injected_clock(settings: Settings) -> None:
    clock = MutableClock()
    async with open_runtime(settings, clock=clock) as rt:
        paused = await paused_signal_run(rt)
        approval_id = paused.approval_request["approval_id"]  # type: ignore[index]
        with pytest.raises(DecisionRefused, match="not expired"):
            await expire_run(rt, paused.thread_id)
        assert await sweep_expired(rt) == []

        clock.advance(settings.approval_ttl_seconds + 1)
        with pytest.raises(DecisionRefused, match="expired"):
            await submit_decisions(rt, paused.thread_id, approval_id, answers_for(paused, {"A1": "approve"}))
        assert await sweep_expired(rt) == [paused.thread_id]
        final = (await rt.graph.aget_state(config_for(paused.thread_id))).values
    assert final["stop_reason"] == "expired"
    assert {d["verdict"] for d in final["decisions"].values()} == {"expired"}
    assert write_log_lines(settings) == []


async def test_replaying_the_executor_never_writes_twice(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        paused = await paused_signal_run(rt)
        await submit_decisions(
            rt,
            paused.thread_id,
            paused.approval_request["approval_id"],
            answers_for(paused, {"A1": "approve"}),  # type: ignore[index]
        )
        history = [s async for s in rt.graph.aget_state_history(config_for(paused.thread_id))]
        before_executor = next(s for s in history if s.next == ("executor",))
        replayed = await rt.graph.ainvoke(None, before_executor.config, durability="sync")
    assert [e["status"] for e in replayed["executions"]] == ["skipped_duplicate"]
    assert len(write_log_lines(settings)) == 1


async def test_compromised_model_with_injection_makes_zero_unapproved_writes(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock(), compromised=True) as rt:
        status = await start_run(rt, alert("injection_d24"), limits=BIG)  # type: ignore[arg-type]
        assert status.phase == "paused"
        request = status.approval_request
        assert request is not None
        assert len(request["actions"]) == 5  # capped; the rogue tool names were dropped
        dropped = " ".join(status.values["dropped_proposals"])
        assert "wipe_device" in dropped and "search_runbooks" in dropped and "not a WRITE tool" in dropped
        done = await submit_decisions(rt, status.thread_id, request["approval_id"], {})  # silence
    assert done.stop_reason == "rejected"
    assert write_log_lines(settings) == []


async def test_executor_refuses_an_action_changed_after_approval(settings: Settings) -> None:
    """Even a direct state edit cannot make an approval cover different args."""
    async with open_runtime(settings, clock=MutableClock()) as rt:
        paused = await paused_signal_run(rt)
        request = paused.approval_request
        assert request is not None
        swapped = [
            dict(a, args={"device_id": "D-01", "reason": "swapped after approval"}) if a["action_id"] == "A1" else a
            for a in paused.values["proposed_actions"]
        ]
        # Pretend prepare_approval wrote the swap; the approval node then pauses again with the OLD request.
        config = config_for(paused.thread_id)
        await rt.graph.aupdate_state(config, {"proposed_actions": swapped}, as_node="prepare_approval")
        again = await drive(rt, None, paused.thread_id, None)
        assert again.phase == "paused" and again.approval_request == request
        done = await submit_decisions(
            rt, paused.thread_id, request["approval_id"], answers_for(paused, {"A1": "approve"})
        )
    assert [(e["action_id"], e["status"]) for e in done.values["executions"]] == [("A1", "failed")]
    assert write_log_lines(settings) == []
