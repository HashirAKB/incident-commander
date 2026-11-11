"""The tool contract, checked live over stdio through langchain-mcp-adapters.

Runs against the Python stub always, and against the real telemetry-ops-copilot
server whenever scripts/fetch_telemetry.sh has vendored it (CI does).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import replace

import httpx
import pytest

from incident_commander.config import VENDOR_TELEMETRY_DIR, Settings
from incident_commander.mcp_bridge import PROJECT_ROOT, open_toolbox, real_telemetry_available, telemetry_db_path
from incident_commander.registry import load_contract

EXPECTED_READ = {
    "list_devices",
    "get_metrics",
    "detect_anomalies",
    "search_runbooks",
    "get_device_events",
    "get_config_changes",
}
EXPECTED_WRITE = {"create_incident", "restart_device"}
REQUIRED_ARGS = {
    "get_metrics": {"device_id"},
    "detect_anomalies": {"target"},
    "search_runbooks": {"query"},
    "create_incident": {"device_id", "title", "severity", "description"},
    "restart_device": {"device_id", "reason"},
    "get_device_events": {"device_id", "since"},
    "get_config_changes": {"target", "since"},
}

BACKENDS = [
    "stub",
    pytest.param(
        "real",
        marks=[
            pytest.mark.real_telemetry,
            pytest.mark.skipif(not real_telemetry_available(), reason="run scripts/fetch_telemetry.sh"),
        ],
    ),
]


def test_contract_file_lists_every_expected_tool() -> None:
    contract = load_contract()
    assert {n for n, c in contract.items() if c.access == "READ"} == EXPECTED_READ
    assert {n for n, c in contract.items() if c.access == "WRITE"} == EXPECTED_WRITE


@pytest.mark.parametrize("backend", BACKENDS)
async def test_servers_expose_the_contract(settings: Settings, backend: str) -> None:
    async with open_toolbox(replace(settings, telemetry_backend=backend)) as toolbox:  # type: ignore[arg-type]
        assert set(toolbox.read_tools) == EXPECTED_READ
        assert set(toolbox.write_tools) == EXPECTED_WRITE
        for name, required in REQUIRED_ARGS.items():
            tool = toolbox.read_tools.get(name) or toolbox.write_tools[name]
            schema = tool.args_schema if isinstance(tool.args_schema, dict) else {}
            assert set(schema.get("required", [])) == required, name


@pytest.mark.parametrize("backend", BACKENDS)
async def test_read_tools_return_the_seeded_fleet(settings: Settings, backend: str) -> None:
    async with open_toolbox(replace(settings, telemetry_backend=backend)) as toolbox:  # type: ignore[arg-type]
        result = await toolbox.read_tools["detect_anomalies"].ainvoke({"target": "D-17"})
        text = result if isinstance(result, str) else json.dumps(result)
        assert "temperature" in text and "D-17" in text

        events = await toolbox.read_tools["get_device_events"].ainvoke(
            {"device_id": "D-33", "since": "2026-02-28T00:00:00Z"}
        )
        assert "link_lost" in str(events)


@pytest.mark.real_telemetry
@pytest.mark.skipif(not real_telemetry_available(), reason="run scripts/fetch_telemetry.sh")
async def test_real_server_over_streamable_http(settings: Settings) -> None:
    """The docker compose setup: telemetry runs as its own service and we speak MCP over HTTP."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    vendor = PROJECT_ROOT / VENDOR_TELEMETRY_DIR
    env = dict(os.environ) | {
        "MCP_PORT": str(port),
        "MCP_API_TOKEN": "contract-token",
        "DB_PATH": str(telemetry_db_path()),
    }
    server = subprocess.Popen(
        [str(vendor / "node_modules/.bin/tsx"), "src/server/http-main.ts"], cwd=vendor, env=env, stdout=subprocess.PIPE
    )
    try:
        assert server.stdout is not None
        server.stdout.readline()  # "telemetry-ops MCP server on http://localhost:<port>/mcp"
        url = f"http://127.0.0.1:{port}/mcp"
        async with open_toolbox(replace(settings, telemetry_url=url, telemetry_token="contract-token")) as toolbox:
            assert set(toolbox.read_tools) == EXPECTED_READ
            assert set(toolbox.write_tools) == EXPECTED_WRITE
        with pytest.raises(ExceptionGroup) as refused:  # wrong token: the server answers 401
            async with open_toolbox(replace(settings, telemetry_url=url, telemetry_token="wrong")):
                pass
        assert refused.group_contains(httpx.HTTPStatusError, match="401")
    finally:
        server.terminate()
        server.wait()
