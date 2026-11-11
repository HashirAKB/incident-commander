"""Mock-mode latency per run: the explicit graph vs the hand-written TS loop.

Both sides use their deterministic mock model, so this measures framework and
process overhead, not model latency (a real model adds seconds per call).

    python -m scripts.latency_compare            # measure, write docs/latency.json, print the table
    python -m scripts.latency_compare --table    # print the table from the committed json only

Cold = a fresh CLI process per run (interpreter start, MCP servers, one alert).
Warm = runs inside one already-started process (graph only; the TS repo has no
equivalent in-process entry point we can call from here).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TS_ROOT = ROOT / "vendor" / "telemetry-ops-copilot"
RESULT_PATH = ROOT / "docs" / "latency.json"
ALERTS = ROOT / "tests" / "fixtures" / "alerts"
COLD_RUNS, WARM_RUNS = 5, 10


def median_ms(samples: list[float]) -> int:
    return round(statistics.median(samples) * 1000)


def timed(command: list[str], cwd: Path, env: dict[str, str]) -> float:
    start = time.perf_counter()
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise RuntimeError(f"{command} failed:\n{result.stdout}\n{result.stderr}")
    return elapsed


def python_cold(tmp: Path) -> int:
    env = dict(os.environ) | {
        "IC_MODE": "mock",
        "IC_TELEMETRY": "stub",
        "IC_DATA_DIR": str(tmp / "cold-data"),
        "IC_RUNS_DIR": str(tmp / "cold-runs"),
        "IC_TRACES_DIR": str(tmp / "cold-traces"),
    }
    command = [sys.executable, "-m", "incident_commander.cli", "run", "--alert", str(ALERTS / "healthy_d02.json")]
    return median_ms([timed(command, ROOT, env) for _ in range(COLD_RUNS)])


def typescript_cold(tmp: Path) -> int | None:
    if not (TS_ROOT / "node_modules").exists():
        return None
    env = dict(os.environ) | {"AGENT_DB_PATH": str(tmp / "agent.db"), "DB_PATH": str(TS_ROOT / "data" / "fleet.db")}
    command = [
        str(TS_ROOT / "node_modules/.bin/tsx"),
        "src/agent/cli.ts",
        "Is D-02 healthy?",
        "--provider",
        "mock",
        "--approve",
        "none",
    ]
    return median_ms([timed(command, TS_ROOT, env) for _ in range(COLD_RUNS)])


async def python_warm(tmp: Path) -> dict[str, int]:
    from incident_commander.config import load_settings
    from incident_commander.runner import load_alert, open_runtime, start_run, submit_decisions

    settings = replace(
        load_settings(),
        mode="mock",
        database_url=None,
        telemetry_backend="stub",
        data_dir=tmp / "warm-data",
        runs_dir=tmp / "warm-runs",
        traces_dir=tmp / "warm-traces",
        write_log=tmp / "warm-writes.jsonl",
    )
    healthy, signal = load_alert(ALERTS / "healthy_d02.json"), load_alert(ALERTS / "signal_d33.json")
    no_action: list[float] = []
    with_approval: list[float] = []
    async with open_runtime(settings) as rt:
        for _ in range(WARM_RUNS):
            start = time.perf_counter()
            await start_run(rt, healthy)
            no_action.append(time.perf_counter() - start)

            start = time.perf_counter()
            paused = await start_run(rt, signal)
            request = paused.approval_request
            assert request is not None
            answers = {a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]}
            await submit_decisions(rt, paused.thread_id, request["approval_id"], answers)
            with_approval.append(time.perf_counter() - start)
    return {"no_action": median_ms(no_action), "approve_two_writes": median_ms(with_approval)}


def measure() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        return {
            "mode": "mock",
            "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
            "cold_runs": COLD_RUNS,
            "warm_runs": WARM_RUNS,
            "median_ms": {
                "graph_cold_cli_healthy_alert": python_cold(tmp),
                "ts_loop_cold_cli_healthy_question": typescript_cold(tmp),
                **{f"graph_warm_{k}": v for k, v in asyncio.run(python_warm(tmp)).items()},
            },
        }


def render(result: dict[str, Any]) -> str:
    values = result["median_ms"]
    ts = values["ts_loop_cold_cli_healthy_question"]
    return "\n".join(
        [
            f"Median wall time in ms, mode={result['mode']}, {result['machine']} "
            f"({result['cold_runs']} cold runs, {result['warm_runs']} warm runs).",
            "",
            "| scenario | hand-written loop (TS) | LangGraph (Python) |",
            "| --- | --- | --- |",
            f"| cold CLI, healthy device, no writes | {ts if ts is not None else 'N/A'} | "
            f"{values['graph_cold_cli_healthy_alert']} |",
            f"| warm, healthy device, no writes | N/A | {values['graph_warm_no_action']} |",
            f"| warm, pause + approve + 2 writes | N/A | {values['graph_warm_approve_two_writes']} |",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", action="store_true", help="print the table from docs/latency.json")
    args = parser.parse_args()
    if not args.table:
        RESULT_PATH.write_text(json.dumps(measure(), indent=2, sort_keys=True) + "\n")
    print(render(json.loads(RESULT_PATH.read_text())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
