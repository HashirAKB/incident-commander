"""Crash tests: kill a process with SIGKILL and finish the run from a new process.

(a) kill `ic run --follow` while paused, approve with `ic approve`
(b) kill `ic run` inside an investigator, `ic resume`, then approve
(c) kill `ic serve` while a run is paused, approve over HTTP on a new server

The `ic` CLI runs in real subprocesses. The only thing they share with each
other and with this test is the checkpointer (SQLite file or Postgres), the
ledger and the MCP server's write log. Parametrized over both checkpointers.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from incident_commander.config import Settings, load_settings
from incident_commander.runner import RunStatus, get_status, open_runtime
from incident_commander.tracing import read_trace
from tests.conftest import ALERTS_DIR, write_log_lines

IC = [sys.executable, "-m", "incident_commander.cli"]


def ic(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([*IC, *args], capture_output=True, text=True, timeout=120, env=env or dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def start_ic(*args: str, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [*IC, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env or dict(os.environ)
    )


async def status_of(thread_id: str) -> RunStatus:
    async with open_runtime(load_settings()) as rt:
        return await get_status(rt, thread_id)


async def wait_for(check: Callable[[], bool], what: str, timeout: float = 90) -> None:
    for _ in range(int(timeout * 4)):
        if check():
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"timed out waiting for {what}")


async def wait_for_phase(thread_id: str, phase: str, proc: subprocess.Popen[str]) -> None:
    for _ in range(90):
        if proc.poll() is not None:
            raise AssertionError(f"ic exited early:\n{proc.stdout.read() if proc.stdout else ''}")
        if (await status_of(thread_id)).phase == phase:
            return
        await asyncio.sleep(1)
    raise AssertionError(f"run never reached {phase}")


def approve_all_file(tmp_path: Path) -> Path:
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps({"A1": {"verdict": "approve"}, "A2": {"verdict": "approve"}}))
    return path


def assert_completed_once(settings: Settings, final: RunStatus) -> None:
    assert final.phase == "finished" and final.stop_reason == "completed"
    assert [e["status"] for e in final.values["executions"]] == ["executed", "executed"]
    writes = [json.loads(line)["tool"] for line in write_log_lines(settings)]
    assert writes == ["restart_device", "create_incident"], "each approved action must run exactly once"
    postmortem = Path(final.values["postmortem_path"]).read_text()
    assert "stop_reason | **completed**" in postmortem


async def test_a_kill_while_paused_then_approve_from_a_new_process(
    settings: Settings, backend: str, tmp_path: Path
) -> None:
    thread_id = str(uuid.uuid4())
    alert = str(ALERTS_DIR / "signal_d33.json")
    follower = start_ic("run", "--alert", alert, "--thread-id", thread_id, "--follow")
    try:
        await wait_for_phase(thread_id, "paused", follower)
        assert follower.poll() is None, "the follower should still be attached while paused"
    finally:
        follower.send_signal(signal.SIGKILL)
        follower.wait()
    assert write_log_lines(settings) == []

    ic("approve", thread_id, "--decisions-file", str(approve_all_file(tmp_path)))
    assert_completed_once(settings, await status_of(thread_id))


async def test_b_kill_mid_investigator_then_resume_from_last_checkpoint(
    settings: Settings, backend: str, tmp_path: Path
) -> None:
    thread_id = str(uuid.uuid4())
    marker = tmp_path / "blocked.marker"
    env = dict(os.environ) | {"IC_FAKE_BLOCK_INVESTIGATOR": "events", "IC_FAKE_BLOCK_MARKER": str(marker)}
    runner_proc = start_ic("run", "--alert", str(ALERTS_DIR / "signal_d33.json"), "--thread-id", thread_id, env=env)
    try:
        await wait_for(marker.exists, "the events investigator to block")
        await asyncio.sleep(3)  # let the other branches finish and record their writes
    finally:
        runner_proc.send_signal(signal.SIGKILL)
        runner_proc.wait()

    assert (await status_of(thread_id)).phase == "interrupted"
    ic("resume", thread_id)  # no blocking hook this time
    assert (await status_of(thread_id)).phase == "paused"
    ic("approve", thread_id, "--decisions-file", str(approve_all_file(tmp_path)))

    final = await status_of(thread_id)
    assert_completed_once(settings, final)
    findings = final.values["findings"]
    assert list(findings) == ["events", "metrics", "runbooks"]
    assert len({f["finding_id"] for f in findings.values()}) == 3
    # Branches that finished before the kill were not re-run on resume.
    tool_spans = [
        s["attributes"]["tool"] for s in read_trace(settings.traces_dir, thread_id) if s["name"].startswith("tool ")
    ]
    assert tool_spans.count("detect_anomalies") == 2
    assert tool_spans.count("get_device_events") == 1


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_for_server(base: str, proc: subprocess.Popen[str]) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(240):
            if proc.poll() is not None:
                raise AssertionError(f"server exited:\n{proc.stdout.read() if proc.stdout else ''}")
            try:
                if (await client.get(f"{base}/runs/pending")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.25)
    raise AssertionError("server did not start")


async def http_phase(base: str, thread_id: str, phase: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        for _ in range(240):
            response = await client.get(f"{base}/runs/{thread_id}")
            if response.status_code == 200 and response.json()["phase"] == phase:
                return dict(response.json())
            await asyncio.sleep(0.25)
    raise AssertionError(f"run never reached {phase}")


async def test_c_kill_the_api_server_while_paused_then_approve_over_http(
    settings: Settings, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPROVER_TOKEN", "crash-test-token")
    auth = {"Authorization": "Bearer crash-test-token"}
    alert = json.loads((ALERTS_DIR / "signal_d33.json").read_text())

    first_port = free_port()
    first = start_ic("serve", "--port", str(first_port))
    try:
        base = f"http://127.0.0.1:{first_port}"
        await wait_for_server(base, first)
        async with httpx.AsyncClient() as client:
            created = await client.post(f"{base}/runs", json={"alert": alert}, headers=auth)
        thread_id = created.json()["thread_id"]
        paused = await http_phase(base, thread_id, "paused")
    finally:
        first.send_signal(signal.SIGKILL)
        first.wait()

    second_port = free_port()
    second = start_ic("serve", "--port", str(second_port))
    try:
        base = f"http://127.0.0.1:{second_port}"
        await wait_for_server(base, second)
        request = paused["approval_request"]
        decisions = {a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base}/runs/{thread_id}/decisions",
                json={"approval_id": request["approval_id"], "decisions": decisions},
                headers=auth,
            )
        assert response.status_code == 202, response.text
        await http_phase(base, thread_id, "finished")
    finally:
        second.terminate()
        second.wait()

    assert_completed_once(settings, await status_of(thread_id))
