"""Connect to the two MCP servers and load their tools as LangChain tools.

We keep one long-lived session per server for the whole process. The simpler
`MultiServerMCPClient.get_tools()` opens a fresh session (a new subprocess for
stdio servers) on every tool call, which costs about a second per call with
the Node server.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools

from incident_commander.config import VENDOR_TELEMETRY_DIR, Settings
from incident_commander.registry import Toolbox, split_tools

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def real_telemetry_available() -> bool:
    vendor = PROJECT_ROOT / VENDOR_TELEMETRY_DIR
    return (vendor / "node_modules/.bin/tsx").exists() and (vendor / "data/fleet.db").exists()


def use_real_telemetry(settings: Settings) -> bool:
    if settings.telemetry_backend == "real":
        if not real_telemetry_available():
            raise RuntimeError("IC_TELEMETRY=real but the server is missing; run scripts/fetch_telemetry.sh")
        return True
    return settings.telemetry_backend == "auto" and real_telemetry_available() and shutil.which("node") is not None


def _python_server(module: str, settings: Settings) -> StdioConnection:
    return StdioConnection(
        transport="stdio",
        command=sys.executable,
        args=["-m", module],
        cwd=str(PROJECT_ROOT),
        # Child processes only see what we pass here (plus PATH/HOME), so no
        # API keys or tokens leak into the MCP servers.
        env={"IC_WRITE_LOG": str(settings.write_log.resolve())},
    )


def connections(settings: Settings) -> dict[str, Connection]:
    if use_real_telemetry(settings):
        vendor = PROJECT_ROOT / VENDOR_TELEMETRY_DIR
        telemetry = StdioConnection(
            transport="stdio",
            command=str(vendor / "node_modules/.bin/tsx"),
            args=["src/server/stdio.ts"],
            cwd=str(vendor),
            env={"DB_PATH": str(telemetry_db_path())},
        )
    else:
        telemetry = _python_server("servers.telemetry_stub.server", settings)
    return {"telemetry": telemetry, "fleet-context": _python_server("servers.fleet_context.server", settings)}


def telemetry_db_path() -> Path:
    return PROJECT_ROOT / VENDOR_TELEMETRY_DIR / "data/fleet.db"


@asynccontextmanager
async def open_toolbox(settings: Settings) -> AsyncIterator[Toolbox]:
    """Start both MCP servers, load and classify their tools, close them on exit."""
    client = MultiServerMCPClient(connections(settings))
    async with AsyncExitStack() as stack:
        tools = []
        for server_name in ("telemetry", "fleet-context"):
            session = await stack.enter_async_context(client.session(server_name))
            tools += await load_mcp_tools(session, server_name=server_name)
        yield split_tools(tools)
