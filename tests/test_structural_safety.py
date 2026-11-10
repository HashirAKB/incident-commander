"""Write gating is structural: WRITE tools exist in exactly one node, the executor.

Three independent checks:
1. Walk every node of the compiled graph (and the investigator subgraph) and
   collect every tool object reachable from it: WRITE tools appear only under
   "executor".
2. Run real incidents with a spy model and record every tool set ever bound
   to a model: none contains a WRITE tool.
3. Grep src/ so a future change that passes `write_tools` somewhere new fails.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from incident_commander.config import Settings
from incident_commander.models.fake import FakeChatModel
from incident_commander.registry import load_contract
from incident_commander.runner import open_runtime, start_run, submit_decisions
from tests.conftest import MutableClock, alert

WRITE_TOOLS = {name for name, c in load_contract().items() if c.access == "WRITE"}
SRC = Path(__file__).resolve().parents[1] / "src" / "incident_commander"


def reachable_tools(obj: Any, seen: set[int] | None = None) -> set[str]:
    """Every BaseTool name reachable from obj through closures, attributes and containers."""
    seen = seen if seen is not None else set()
    if id(obj) in seen or obj is None or isinstance(obj, (str, bytes, int, float, bool, Path)):
        return set()
    seen.add(id(obj))
    if isinstance(obj, BaseTool):
        return {obj.name}
    found: set[str] = set()
    if isinstance(obj, CompiledStateGraph):
        for spec in obj.builder.nodes.values():
            found |= reachable_tools(spec.runnable, seen)
        return found
    if isinstance(obj, dict):
        children: list[Any] = list(obj.values())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        children = list(obj)
    else:
        children = [cell.cell_contents for cell in (getattr(obj, "__closure__", None) or ())]
        children += [getattr(obj, attr, None) for attr in ("func", "afunc", "bound", "__wrapped__")]
        if hasattr(obj, "__dataclass_fields__"):
            children += [getattr(obj, name) for name in obj.__dataclass_fields__]
    for child in children:
        found |= reachable_tools(child, seen)
    return found


async def test_write_tools_are_reachable_only_from_the_executor(settings: Settings) -> None:
    async with open_runtime(settings) as rt:
        nodes = rt.graph.builder.nodes
        reach = {name: reachable_tools(spec.runnable) for name, spec in nodes.items()}
    assert reach["executor"] >= WRITE_TOOLS, "the executor must hold the write tools"
    for name, tools in reach.items():
        if name != "executor":
            assert not tools & WRITE_TOOLS, f"node {name} can reach write tools {tools & WRITE_TOOLS}"
    assert reach["investigate"], "investigators should hold their read tools"


class SpyModel(FakeChatModel):
    """Records the names of every tool set bound to it."""

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        BOUND.append(sorted(t.name for t in tools))
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)


BOUND: list[list[str]] = []


async def test_no_model_is_ever_bound_to_a_write_tool(settings: Settings) -> None:
    BOUND.clear()
    async with open_runtime(settings, clock=MutableClock(), model=SpyModel(compromised=True)) as rt:
        for name in ("signal_d33", "injection_d24", "battery_fleet"):
            status = await start_run(rt, alert(name))
            if status.phase == "paused":
                request = status.approval_request
                assert request is not None
                approve_all = {
                    a["action_id"]: {"verdict": "approve", "args_hash": a["args_hash"]} for a in request["actions"]
                }
                await submit_decisions(rt, status.thread_id, request["approval_id"], approve_all)
    assert BOUND, "the spy saw no tool binding at all"
    assert all(not set(names) & WRITE_TOOLS for names in BOUND)


def test_write_tools_are_referenced_only_where_expected() -> None:
    allowed = {"registry.py", "graph.py", "nodes/executor.py"}
    offenders = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if re.search(r"\bwrite_tools\b", path.read_text()) and relative not in allowed:
            offenders.append(relative)
    assert offenders == []
    graph_source = (SRC / "graph.py").read_text()
    # graph.py may pass write tools to the executor and read their schemas, nothing else.
    uses = re.findall(r".*\bwrite_tools\b.*", graph_source)
    assert all("make_executor" in line or "args_schema" in line or "WRITE" in line for line in uses), uses
