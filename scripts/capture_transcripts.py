"""Capture text transcripts of real runs for the README (instead of screenshots).

    python -m scripts.capture_transcripts

Writes docs/screenshots/cli.txt, sse.txt and approval_http.txt. Everything runs
in mock mode against the Python telemetry stub, in a temporary directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
ALERT = ROOT / "tests" / "fixtures" / "alerts" / "signal_d33.json"
TOKEN = "demo-token"


def environment(tmp: Path) -> dict[str, str]:
    return dict(os.environ) | {
        "IC_MODE": "mock",
        "IC_TELEMETRY": "stub",
        "IC_DATA_DIR": str(tmp / "data"),
        "IC_RUNS_DIR": str(tmp / "runs"),
        "IC_TRACES_DIR": str(tmp / "traces"),
        "IC_WRITE_LOG": str(tmp / "writes.jsonl"),
        "APPROVER_TOKEN": TOKEN,
    }


def ic(env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "incident_commander.cli", *args], env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


def shown(command: str, output: str, tmp: Path) -> str:
    return f"$ {command}\n{output.replace(str(tmp), '<tmp>')}\n"


def capture_cli(tmp: Path) -> str:
    env = environment(tmp)
    text = shown(
        "ic run --alert tests/fixtures/alerts/signal_d33.json", run := ic(env, "run", "--alert", str(ALERT)), tmp
    )
    thread_id = next(line.split()[1] for line in run.splitlines() if line.startswith("thread_id:"))
    text += shown("ic pending", ic(env, "pending"), tmp)
    decisions = tmp / "decisions.json"
    decisions.write_text(json.dumps({"A1": {"verdict": "approve"}, "A2": {"verdict": "reject"}}))
    text += shown(
        f"ic approve {thread_id} --decisions-file decisions.json   # A1 approve, A2 reject",
        ic(env, "approve", thread_id, "--decisions-file", str(decisions)),
        tmp,
    )
    text += shown(f"ic history {thread_id}", ic(env, "history", thread_id), tmp)
    trace_lines = ic(env, "trace", thread_id).splitlines()
    text += shown(f"ic trace {thread_id} | head -25", "\n".join(trace_lines[:25]) + "\n", tmp)
    text += f"$ cat {tmp.name}/writes.jsonl   # the telemetry server's own write log\n".replace(tmp.name, "<tmp>")
    text += (tmp / "writes.jsonl").read_text()
    return text


def capture_http(tmp: Path) -> tuple[str, str]:
    os.environ.update(environment(tmp))
    from fastapi.testclient import TestClient

    from incident_commander.api import create_app
    from incident_commander.config import load_settings

    auth = {"Authorization": f"Bearer {TOKEN}"}
    http = ""
    with TestClient(create_app(load_settings())) as client:
        alert = json.loads(ALERT.read_text())
        response = client.post("/runs", json={"alert": alert}, headers=auth)
        http += f"POST /runs -> {response.status_code} {response.json()}\n\n"
        thread_id = response.json()["thread_id"]
        for _ in range(400):
            run = client.get(f"/runs/{thread_id}").json()
            if run["phase"] == "paused":
                break
        request = run["approval_request"]
        http += f"GET /runs/{thread_id} -> phase={run['phase']} diagnosis={run['diagnosis']['root_cause']}\n"
        http += json.dumps(request, indent=2) + "\n\n"

        sse = f"GET /runs/{thread_id}/events\n\n"
        with client.stream("GET", f"/runs/{thread_id}/events") as stream:
            for line in stream.iter_lines():
                sse += (line[:300] + " ...") if len(line) > 300 else line
                sse += "\n"

        answers = {a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]}
        stale = client.post(
            f"/runs/{thread_id}/decisions", json={"approval_id": "old", "decisions": answers}, headers=auth
        )
        http += f"POST /runs/{thread_id}/decisions with a stale approval_id -> {stale.status_code} {stale.json()}\n"
        no_token = client.post(f"/runs/{thread_id}/decisions", json={})
        http += f"POST /runs/{thread_id}/decisions without a token -> {no_token.status_code} {no_token.json()}\n"
        ok = client.post(
            f"/runs/{thread_id}/decisions",
            json={"approval_id": request["approval_id"], "decisions": answers},
            headers=auth,
        )
        http += f"POST /runs/{thread_id}/decisions (approve both) -> {ok.status_code} {ok.json()}\n"
        for _ in range(400):
            final = client.get(f"/runs/{thread_id}").json()
            if final["phase"] == "finished":
                break
        http += f"GET /runs/{thread_id} -> stop_reason={final['stop_reason']} executions="
        http += json.dumps([{k: e[k] for k in ("action_id", "tool", "status")} for e in final["executions"]]) + "\n"
    return sse, http


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    header = "# Captured by scripts/capture_transcripts.py (mode=mock, telemetry stub). Real output, not edited.\n\n"
    with tempfile.TemporaryDirectory() as directory:
        (OUT / "cli.txt").write_text(header + capture_cli(Path(directory)))
    with tempfile.TemporaryDirectory() as directory:
        sse, http = capture_http(Path(directory))
        (OUT / "sse.txt").write_text(header + sse)
        (OUT / "approval_http.txt").write_text(header + http)
    print(f"wrote {', '.join(p.name for p in sorted(OUT.glob('*.txt')))}")


if __name__ == "__main__":
    main()
