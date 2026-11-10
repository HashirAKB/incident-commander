"""READ/WRITE classification of every MCP tool, parsed from docs/mcp-contract.md.

Why parse a markdown file? The contract is reviewed like code and read by
humans; keeping the machine copy in the same table means they cannot drift.
A tool missing from the table is rejected, so a server cannot sneak in a new
write tool by giving it a harmless name or a false readOnlyHint.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from langchain_core.tools import BaseTool

Access = Literal["READ", "WRITE"]
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "mcp-contract.md"


class UnknownToolError(RuntimeError):
    """A server offered a tool that the contract does not list."""


@dataclass(frozen=True)
class ToolContract:
    name: str
    server: str
    access: Access
    investigator: str  # which investigator may use it; "none" for write tools


def parse_contract(markdown: str) -> dict[str, ToolContract]:
    body = markdown.split("<!-- contract:start -->")[1].split("<!-- contract:end -->")[0]
    rows = [line for line in body.strip().splitlines() if line.startswith("|")][2:]  # skip header
    contracts: dict[str, ToolContract] = {}
    for row in rows:
        name, server, access, _args, investigator = (cell.strip() for cell in row.strip("|").split("|"))
        if access not in ("READ", "WRITE"):
            raise ValueError(f"Contract row for {name!r} has invalid access {access!r}")
        contracts[name] = ToolContract(name, server, access, investigator)  # type: ignore[arg-type]
    return contracts


@cache
def load_contract() -> dict[str, ToolContract]:
    return parse_contract(CONTRACT_PATH.read_text(encoding="utf-8"))


def classify(tool_name: str) -> Access:
    contract = load_contract().get(tool_name)
    if contract is None:
        raise UnknownToolError(f"Tool {tool_name!r} is not in docs/mcp-contract.md; refusing to use it")
    return contract.access


def tools_for_investigator(investigator: str) -> list[str]:
    """READ tool names an investigator may call. Never includes a WRITE tool."""
    return sorted(c.name for c in load_contract().values() if c.investigator == investigator and c.access == "READ")


@dataclass(frozen=True)
class Toolbox:
    """Tools split by access class. Only the executor ever receives `write_tools`."""

    read_tools: dict[str, BaseTool]
    write_tools: dict[str, BaseTool]


def split_tools(tools: list[BaseTool]) -> Toolbox:
    read: dict[str, BaseTool] = {}
    write: dict[str, BaseTool] = {}
    for tool in tools:
        (write if classify(tool.name) == "WRITE" else read)[tool.name] = tool
    return Toolbox(read_tools=read, write_tools=write)
