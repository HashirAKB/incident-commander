"""One real-model run. Skips itself without ANTHROPIC_API_KEY or network to the API."""

from __future__ import annotations

import socket

import pytest

from incident_commander.config import load_settings
from incident_commander.runner import open_runtime, start_run
from tests.conftest import LIVE_API_KEY, MutableClock, alert, write_log_lines

API_HOST = "api.anthropic.com"
ROOT_CAUSES = {"thermal", "power", "connectivity", "mechanical", "config_regression", "unknown", "no_fault"}


def api_reachable() -> bool:
    try:
        with socket.create_connection((API_HOST, 443), timeout=3):
            return True
    except OSError:
        return False


@pytest.mark.live
@pytest.mark.skipif(not LIVE_API_KEY, reason="ANTHROPIC_API_KEY not set")
async def test_live_model_diagnoses_an_overheating_device(monkeypatch: pytest.MonkeyPatch) -> None:
    if not api_reachable():
        pytest.skip(f"{API_HOST} is not reachable")
    monkeypatch.setenv("ANTHROPIC_API_KEY", LIVE_API_KEY or "")
    monkeypatch.setenv("IC_MODE", "live")
    settings = load_settings()
    assert settings.mode == "live"
    async with open_runtime(settings, clock=MutableClock()) as rt:
        status = await start_run(rt, alert("thermal_d17"))
    assert status.values["mode"] == "live"
    assert status.phase in ("paused", "finished")
    assert status.values["diagnosis"]["root_cause"] in ROOT_CAUSES
    assert write_log_lines(settings) == []  # nothing runs without approval, live model or not
