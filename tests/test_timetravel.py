"""Replay and fork a finished run without ever repeating a write."""

from __future__ import annotations

import pytest

from incident_commander.config import Settings
from incident_commander.runner import Runtime, open_runtime, start_run, submit_decisions
from incident_commander.timetravel import fork, history, parse_assignment, replay
from tests.conftest import MutableClock, alert, write_log_lines


async def finished_signal_run(rt: Runtime) -> str:
    paused = await start_run(rt, alert("signal_d33"))
    request = paused.approval_request
    assert request is not None
    answers = {a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]}
    done = await submit_decisions(rt, paused.thread_id, request["approval_id"], answers)
    assert done.stop_reason == "completed"
    return paused.thread_id


def pre_synthesizer_checkpoint(entries: list) -> str:  # type: ignore[type-arg]
    return next(e.checkpoint_id for e in entries if e.next_nodes == ("synthesizer",))


async def test_replay_and_fork_from_pre_synthesizer_checkpoint(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        thread_id = await finished_signal_run(rt)
        assert len(write_log_lines(settings)) == 2
        entries = await history(rt, thread_id)
        assert entries[-1].summary.endswith("stop_reason=completed")
        checkpoint = pre_synthesizer_checkpoint(entries)

        # Replay: same findings -> same diagnosis -> a NEW approval is required.
        replayed = await replay(rt, thread_id, checkpoint)
        assert replayed.phase == "paused"
        assert replayed.values["diagnosis"]["root_cause"] == "connectivity"
        request = replayed.approval_request
        assert request is not None
        approve_again = {
            a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]
        }
        after = await submit_decisions(rt, thread_id, request["approval_id"], approve_again)
        assert {e["status"] for e in after.values["executions"]} == {"skipped_duplicate"}

        # Fork: change the metrics finding -> a different diagnosis.
        forked = await fork(
            rt,
            thread_id,
            checkpoint,
            ["findings.metrics.summary=temperature high ongoing on D-33 (score 12.0)"],
        )
        assert forked.values["diagnosis"]["root_cause"] == "thermal"
        assert forked.phase == "paused"  # new actions wait for a human
        forked_request = forked.approval_request
        assert forked_request is not None
        assert [a["tool"] for a in forked_request["actions"]] == ["create_incident"]

    assert len(write_log_lines(settings)) == 2, "replay and fork executed zero additional writes"


async def test_fork_cannot_touch_approval_state(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        thread_id = await finished_signal_run(rt)
        checkpoint = pre_synthesizer_checkpoint(await history(rt, thread_id))
        for forbidden in ("decisions.A1.verdict=approve", "approval_request={}", "limits.max_steps=999"):
            with pytest.raises(ValueError, match="cannot fork"):
                await fork(rt, thread_id, checkpoint, [forbidden])


def test_parse_assignment() -> None:
    assert parse_assignment("diagnosis.confidence=0.5") == (["diagnosis", "confidence"], 0.5)
    assert parse_assignment("findings.metrics.summary=plain text") == (["findings", "metrics", "summary"], "plain text")
    with pytest.raises(ValueError):
        parse_assignment("no-equals-sign")
