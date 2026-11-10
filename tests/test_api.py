"""HTTP API: auth, refusals (409), SSE order, sweeper, bind check."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from incident_commander.api import check_bind, create_app
from incident_commander.config import load_settings
from tests.conftest import ALERTS_DIR, MutableClock, write_log_lines

TOKEN = "test-approver-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SIGNAL_ALERT = json.loads((ALERTS_DIR / "signal_d33.json").read_text())


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, clock: MutableClock) -> Iterator[TestClient]:
    monkeypatch.setenv("APPROVER_TOKEN", TOKEN)
    with TestClient(create_app(load_settings(), clock=clock, sweep_interval_seconds=0.2)) as test_client:
        yield test_client


def wait_for_phase(client: TestClient, thread_id: str, *phases: str) -> dict[str, Any]:
    for _ in range(300):
        response = client.get(f"/runs/{thread_id}")
        if response.status_code == 200 and response.json()["phase"] in phases:
            return dict(response.json())
        time.sleep(0.05)
    raise AssertionError(f"run never reached {phases}")


def start_paused_run(client: TestClient) -> dict[str, Any]:
    response = client.post("/runs", json={"alert": SIGNAL_ALERT}, headers=AUTH)
    assert response.status_code == 202
    return wait_for_phase(client, response.json()["thread_id"], "paused")


def approve_all(run: dict[str, Any]) -> dict[str, Any]:
    request = run["approval_request"]
    decisions = {a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]}
    return {"approval_id": request["approval_id"], "decisions": decisions}


def test_mutating_endpoints_need_the_token(client: TestClient) -> None:
    assert client.post("/runs", json={"alert": SIGNAL_ALERT}).status_code == 401
    assert (
        client.post("/runs", json={"alert": SIGNAL_ALERT}, headers={"Authorization": "Bearer nope"}).status_code == 401
    )
    some_id = str(uuid.uuid4())
    assert client.post(f"/runs/{some_id}/decisions", json={}).status_code == 401


def test_approvals_disabled_without_a_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPROVER_TOKEN", raising=False)
    with TestClient(create_app(load_settings())) as no_token_client:
        response = no_token_client.post("/runs", json={"alert": SIGNAL_ALERT}, headers=AUTH)
    assert response.status_code == 503


def test_bad_input_is_rejected(client: TestClient) -> None:
    assert client.get("/runs/../../etc").status_code in (400, 404)
    assert client.get("/runs/not-a-uuid").status_code == 400
    assert client.get(f"/runs/{uuid.uuid4()}").status_code == 404
    bad_alert = SIGNAL_ALERT | {"target": "all devices"}
    assert client.post("/runs", json={"alert": bad_alert}, headers=AUTH).status_code == 422


def test_full_flow_with_409s_then_approval(client: TestClient) -> None:
    run = start_paused_run(client)
    thread_id = run["thread_id"]
    assert [r["thread_id"] for r in client.get("/runs/pending").json()] == [thread_id]
    body = approve_all(run)

    stale = body | {"approval_id": "old-approval"}
    assert client.post(f"/runs/{thread_id}/decisions", json=stale, headers=AUTH).status_code == 409
    tampered = json.loads(json.dumps(body))
    tampered["decisions"]["A1"]["args_hash"] = "0" * 64
    assert client.post(f"/runs/{thread_id}/decisions", json=tampered, headers=AUTH).status_code == 409
    added = json.loads(json.dumps(body))
    added["decisions"]["A9"] = {"verdict": "approve", "args_hash": "x"}
    assert client.post(f"/runs/{thread_id}/decisions", json=added, headers=AUTH).status_code == 409
    assert write_log_lines(load_settings()) == []

    assert client.post(f"/runs/{thread_id}/decisions", json=body, headers=AUTH).status_code == 202
    final = wait_for_phase(client, thread_id, "finished")
    assert final["stop_reason"] == "completed"
    assert [e["status"] for e in final["executions"]] == ["executed", "executed"]
    # The same decision again: the run no longer waits for anything.
    assert client.post(f"/runs/{thread_id}/decisions", json=body, headers=AUTH).status_code == 409


def test_decision_after_expiry_is_409_and_sweeper_expires_the_run(client: TestClient, clock: MutableClock) -> None:
    run = start_paused_run(client)
    clock.advance(load_settings().approval_ttl_seconds + 1)
    response = client.post(f"/runs/{run['thread_id']}/decisions", json=approve_all(run), headers=AUTH)
    assert response.status_code == 409
    final = wait_for_phase(client, run["thread_id"], "finished")  # the 0.2 s sweeper expires it
    assert final["stop_reason"] == "expired"
    assert write_log_lines(load_settings()) == []


def test_sse_sends_snapshot_then_node_events_in_order(client: TestClient) -> None:
    run = start_paused_run(client)
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream("GET", f"/runs/{run['thread_id']}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((name, json.loads(line.removeprefix("data: "))))
    assert events[0][0] == "snapshot" and events[0][1]["phase"] == "paused"
    nodes = [data["node"] for name, data in events if name == "node"]
    assert nodes == ["triage", "investigate", "investigate", "investigate", "gather", "synthesizer", "prepare_approval"]
    assert [name for name, _ in events][-2:] == ["approval_requested", "end"]


def test_approval_page_is_served(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "EventSource" in page.text and "sessionStorage" in page.text
    assert "innerHTML" not in page.text  # untrusted content is only ever set with textContent


def test_api_refuses_to_bind_publicly_without_a_token() -> None:
    with pytest.raises(SystemExit):
        check_bind("0.0.0.0", None)
    check_bind("127.0.0.1", None)
    check_bind("0.0.0.0", "a-token")
