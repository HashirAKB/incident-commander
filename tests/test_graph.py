"""The graph end to end over stdio MCP servers (stub) with the fake model."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from incident_commander import routing
from incident_commander.config import Settings
from incident_commander.runner import config_for, open_runtime, start_run, submit_decisions
from incident_commander.state import IncidentState, Limits
from tests.conftest import alert, fixed_clock

BIG: Limits = {"max_steps": 100, "max_tokens": 1_000_000, "max_cost_usd": 100.0}


async def test_thermal_alert_pauses_for_approval_then_writes_postmortem(settings: Settings) -> None:
    async with open_runtime(settings, clock=fixed_clock) as rt:
        status = await start_run(rt, alert("thermal_d17"), limits=BIG)
        assert status.phase == "paused"
        assert set(status.values["findings"]) == {"metrics", "events", "runbooks"}
        assert status.values["diagnosis"]["root_cause"] == "thermal"
        request = status.approval_request
        assert request is not None and [a["tool"] for a in request["actions"]] == ["create_incident"]
        done = await submit_decisions(rt, status.thread_id, request["approval_id"], {"A1": {"verdict": "reject"}})
    assert done.phase == "finished" and done.stop_reason == "rejected"
    postmortem = Path(done.values["postmortem_path"]).read_text()
    assert "DRAFT" in postmortem and "mode=mock" in postmortem


async def test_healthy_device_needs_no_action(settings: Settings) -> None:
    async with open_runtime(settings, clock=fixed_clock) as rt:
        status = await start_run(rt, alert("healthy_d02"), limits=BIG)
    assert status.stop_reason == "no_action_needed"
    assert status.values["proposed_actions"] == []


async def test_two_runs_with_same_input_are_byte_identical(settings: Settings) -> None:
    outputs = []
    async with open_runtime(settings, clock=fixed_clock) as rt:
        for _ in range(2):
            paused = await start_run(rt, alert("signal_d33"), limits=BIG)
            request = paused.approval_request
            assert request is not None
            answers = {a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]}
            done = await submit_decisions(rt, paused.thread_id, request["approval_id"], answers)
            postmortem = Path(done.values["postmortem_path"]).read_text()
            values = json.dumps({k: v for k, v in done.values.items() if k != "postmortem_path"}, sort_keys=True)
            text = values + postmortem
            # Only the two random ids differ between runs.
            outputs.append(text.replace(done.thread_id, "<thread>").replace(request["approval_id"], "<approval>"))
    assert outputs[0] == outputs[1]


async def test_replaying_investigators_never_duplicates_findings(settings: Settings) -> None:
    async with open_runtime(settings, clock=fixed_clock) as rt:
        status = await start_run(rt, alert("thermal_d17"), limits=BIG)
        history = [s async for s in rt.graph.aget_state_history(config_for(status.thread_id))]
        # The checkpoint taken just before the investigators ran: its next step is "investigate".
        before_fan_out = next(s for s in history if "investigate" in s.next)
        await rt.graph.ainvoke(None, before_fan_out.config, durability="sync")
        final = (await rt.graph.aget_state(config_for(status.thread_id))).values
    assert sorted(final["findings"]) == ["events", "metrics", "runbooks"]
    assert [f["finding_id"] for f in final["findings"].values()] == ["F-events", "F-metrics", "F-runbooks"]


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"max_steps": 2, "max_tokens": 1_000_000, "max_cost_usd": 100.0}, "step_limit"),
        ({"max_steps": 100, "max_tokens": 3000, "max_cost_usd": 100.0}, "token_limit"),
        ({"max_steps": 100, "max_tokens": 1_000_000, "max_cost_usd": 0.000001}, "cost_limit"),
    ],
)
async def test_each_budget_limit_stops_the_run(settings: Settings, limits: Limits, expected: str) -> None:
    async with open_runtime(settings, clock=fixed_clock) as rt:
        status = await start_run(rt, alert("thermal_d17"), limits=limits)
    assert status.phase == "finished"
    assert status.stop_reason == expected
    assert Path(status.values["postmortem_path"]).exists()


async def test_compromised_model_write_calls_are_blocked_in_investigators(settings: Settings) -> None:
    async with open_runtime(settings, clock=fixed_clock, compromised=True) as rt:
        status = await start_run(rt, alert("injection_d24"), limits=BIG)
    blocked = [c for f in status.values["findings"].values() for c in f["blocked_tool_calls"]]
    assert blocked and all(c.startswith("restart_device") for c in blocked)
    assert not Path(settings.write_log).exists()  # the server never saw a write


def test_fan_out_splits_remaining_budget_across_branches() -> None:
    state: IncidentState = {
        "alert": alert("thermal_d17"),
        "mode": "mock",
        "started_at": "x",
        "limits": {"max_steps": 31, "max_tokens": 9000, "max_cost_usd": 0.3},
        "approval_ttl_seconds": 60,
        "findings": {},
        "usage": {"steps": 1, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        "triage": {
            "category": "thermal",
            "severity": "high",
            "investigators": ["metrics", "events", "runbooks"],
            "rationale": "",
        },
    }
    sends = routing.fan_out_investigators(state)
    assert isinstance(sends, list) and len(sends) == 3
    assert all(s.arg["branch_budget"] == {"max_steps": 10, "max_tokens": 3000, "max_cost_usd": 0.1} for s in sends)


def test_fan_out_stops_when_over_budget() -> None:
    state = {
        "limits": {"max_steps": 1, "max_tokens": 10, "max_cost_usd": 1.0},
        "usage": {"steps": 1, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
    }
    assert routing.fan_out_investigators(state) == "budget_stop"  # type: ignore[arg-type]


def test_thread_ids_are_uuids() -> None:
    with pytest.raises(ValueError):
        config_for("../escape")
    assert config_for(str(uuid.UUID(int=1)))["configurable"]["thread_id"]


async def test_a_new_run_cannot_reuse_an_existing_thread_id(settings: Settings) -> None:
    async with open_runtime(settings, clock=fixed_clock) as rt:
        status = await start_run(rt, alert("healthy_d02"), limits=BIG)
        with pytest.raises(ValueError, match="already exists"):
            await start_run(rt, alert("thermal_d17"), thread_id=status.thread_id)


async def test_recorded_model_responses_drive_a_full_run(settings: Settings) -> None:
    """tests/fixtures/model_recordings/rec-unknown-d11 overrides the fake's rules for triage and synthesis."""
    async with open_runtime(settings, clock=fixed_clock) as rt:
        status = await start_run(rt, alert("recorded_unknown_d11"), limits=BIG)
    assert status.values["triage"]["severity"] == "low"
    assert list(status.values["findings"]) == ["events", "metrics"]
    assert status.values["diagnosis"]["summary"].startswith("Recorded response")
    assert status.stop_reason == "no_action_needed"
