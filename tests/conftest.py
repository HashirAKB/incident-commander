"""Shared fixtures. Every test runs in mock mode with its own data directories."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from incident_commander.config import Settings, load_settings
from incident_commander.state import Alert

# Postgres is opt-in: tests that want it ask for the `backend` fixture. Everything
# else runs on SQLite even when DATABASE_URL is set (as it is in the CI Postgres job).
POSTGRES_URL = os.environ.get("DATABASE_URL")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Parametrize a test over both checkpointers. Postgres skips without DATABASE_URL."""
    if request.param == "postgres":
        if not POSTGRES_URL:
            pytest.skip("DATABASE_URL not set")
        monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    return str(request.param)


class MutableClock:
    """A clock tests can move forward without sleeping."""

    def __init__(self, start: str = FIXED_NOW) -> None:
        from incident_commander.timeutil import parse_iso

        self.now = parse_iso(start)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self.now += timedelta(seconds=seconds)


def write_log_lines(settings: Settings) -> list[str]:
    """Writes as recorded by the MCP server itself (the stub's write log)."""
    path = settings.write_log
    return path.read_text().splitlines() if path.exists() else []
