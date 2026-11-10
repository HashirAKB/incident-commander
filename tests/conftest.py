"""Shared fixtures. Every test runs in mock mode with its own data directories."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from incident_commander.config import Settings, load_settings
from incident_commander.state import Alert


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("IC_MODE", "mock")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("IC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("IC_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("IC_TRACES_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("IC_WRITE_LOG", str(tmp_path / "data" / "writes.jsonl"))
    monkeypatch.setenv("IC_TELEMETRY", "stub")
    yield


@pytest.fixture
def settings() -> Settings:
    return load_settings()


FIXED_NOW = "2026-03-01T00:05:00Z"
ALERTS_DIR = Path(__file__).parent / "fixtures" / "alerts"


def fixed_clock() -> datetime:
    from incident_commander.timeutil import parse_iso

    return parse_iso(FIXED_NOW)


def alert(name: str) -> Alert:
    from incident_commander.runner import load_alert

    return load_alert(ALERTS_DIR / f"{name}.json")
