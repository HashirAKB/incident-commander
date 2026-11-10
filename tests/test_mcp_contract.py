"""The tool contract, checked live over stdio through langchain-mcp-adapters.

Runs against the Python stub always, and against the real telemetry-ops-copilot
server whenever scripts/fetch_telemetry.sh has vendored it (CI does).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from incident_commander.config import Settings
from incident_commander.mcp_bridge import open_toolbox, real_telemetry_available
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
