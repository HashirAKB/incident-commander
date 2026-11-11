"""Spans for nodes, model calls and tool calls; secrets never written anywhere."""

from __future__ import annotations

import os

import pytest

from incident_commander.config import Settings
from incident_commander.runner import open_runtime, start_run
from incident_commander.tracing import configure_langsmith, format_timeline, read_trace, redact
from tests.conftest import MutableClock, alert

FAKE_KEY = "sk-ant-api03-THIS-IS-NOT-A-REAL-KEY-0123456789"
FAKE_TOKEN = "approver-token-should-never-appear"


async def test_trace_has_node_model_and_tool_spans(settings: Settings) -> None:
    async with open_runtime(settings, clock=MutableClock()) as rt:
        status = await start_run(rt, alert("healthy_d02"))
    records = read_trace(settings.traces_dir, status.thread_id)
    kinds = {r["attributes"]["kind"] for r in records}
    assert kinds == {"node", "model", "tool"}
    assert all(r["attributes"]["thread_id"] == status.thread_id for r in records)
    assert all(r["attributes"]["mode"] == "mock" for r in records)
    model_spans = [r for r in records if r["attributes"]["kind"] == "model"]
    assert all(r["attributes"]["tokens.input"] > 0 for r in model_spans)
    tool_names = {r["attributes"]["tool"] for r in records if r["attributes"]["kind"] == "tool"}
    assert {"detect_anomalies", "get_metrics", "search_runbooks"} <= tool_names
    postmortem_span = next(r for r in records if r["name"] == "node postmortem")
    assert postmortem_span["attributes"]["stop_reason"] == "no_action_needed"
    timeline = format_timeline(records)
    assert any("node triage" in line for line in timeline)


async def test_secrets_never_reach_traces_checkpoints_or_postmortems(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)  # IC_MODE=mock keeps the fake model in use
    monkeypatch.setenv("APPROVER_TOKEN", FAKE_TOKEN)
    async with open_runtime(settings, clock=MutableClock()) as rt:
        await start_run(rt, alert("thermal_d17"))
    files = [
        *settings.traces_dir.rglob("*"),
        *settings.runs_dir.rglob("*"),
        *settings.data_dir.rglob("*"),
    ]
    for path in files:
        if path.is_file():
            content = path.read_bytes()
            assert FAKE_KEY.encode() not in content, path
            assert FAKE_TOKEN.encode() not in content, path


def test_redact_masks_keys_and_bearer_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVER_TOKEN", FAKE_TOKEN)
    text = f"key={FAKE_KEY} header=Bearer abc.def token={FAKE_TOKEN}"
    cleaned = redact(text)
    assert FAKE_KEY not in cleaned and "abc.def" not in cleaned and FAKE_TOKEN not in cleaned


def test_langsmith_is_on_only_with_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert configure_langsmith() is False
    assert os.environ["LANGSMITH_TRACING"] == "false"
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    assert configure_langsmith() is True
