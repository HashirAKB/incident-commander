"""Full incident against the REAL telemetry-ops-copilot server (pinned SHA, stdio).

Writes are counted from that server's own SQLite tables (device_events, incidents),
on a copy of its database so the vendored one stays clean. Runs when the server
is vendored (scripts/fetch_telemetry.sh); CI vendors it.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from incident_commander import mcp_bridge
from incident_commander.config import Settings
from incident_commander.runner import open_runtime, start_run, submit_decisions
from tests.conftest import MutableClock, alert

pytestmark = [
    pytest.mark.real_telemetry,
    pytest.mark.skipif(not mcp_bridge.real_telemetry_available(), reason="run scripts/fetch_telemetry.sh"),
]


def server_writes(db: Path) -> tuple[int, int]:
    with sqlite3.connect(db) as connection:
        restarts = connection.execute("SELECT COUNT(*) FROM device_events WHERE type = 'restart'").fetchone()[0]
        incidents = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    return int(restarts), int(incidents)


async def test_signal_incident_end_to_end_on_the_real_server(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "fleet.db"
    shutil.copy(mcp_bridge.telemetry_db_path(), db)
    monkeypatch.setattr(mcp_bridge, "telemetry_db_path", lambda: db)
    before = server_writes(db)

    async with open_runtime(replace(settings, telemetry_backend="real"), clock=MutableClock()) as rt:
        paused = await start_run(rt, alert("signal_d33"))
        assert paused.phase == "paused"
        assert paused.values["diagnosis"]["root_cause"] == "connectivity"
        assert server_writes(db) == before  # nothing written before approval
        request = paused.approval_request
        assert request is not None
        answers = {a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]}
        done = await submit_decisions(rt, paused.thread_id, request["approval_id"], answers)

    assert done.stop_reason == "completed"
    assert server_writes(db) == (before[0] + 1, before[1] + 1)  # exactly one restart, one incident
