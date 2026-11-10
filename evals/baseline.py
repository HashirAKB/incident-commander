"""Baseline for comparison: LangGraph's prebuilt create_react_agent.

This is the ONLY place the prebuilt agent is used. It gets the same MCP tools
as the explicit graph, and every WRITE tool is wrapped so that calling it
first pauses with interrupt() for the same kind of human decision.

Two differences from the explicit graph are on purpose (they are what the eval
compares): the model holds the write tools itself, so safety depends on the
wrapper alone; and there is one loop instead of parallel investigators.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from langgraph.types import interrupt

from incident_commander.actions import InvalidAction, validate_write_args
from incident_commander.llm import call_tool
from incident_commander.prompts import UNTRUSTED_DATA_RULE
from incident_commander.registry import Toolbox

BASELINE_PROMPT = f"""You are an on-call assistant for an IoT fleet. Investigate the alert with the read-only
tools, decide the root cause (thermal, power, connectivity, mechanical, config_regression,
unknown, no_fault), and call create_incident or restart_device only when they help; a human
approves each write. End with a line 'root_cause: <label>'.
{UNTRUSTED_DATA_RULE}"""


def approval_gated(tool: BaseTool, write_schemas: dict[str, dict[str, Any]]) -> BaseTool:
    """Same name and schema as `tool`, but every call waits for a human decision first."""

    async def gated(**args: Any) -> str:
        # Nothing before interrupt(): this function runs again from the top on resume.
        decision = interrupt({"tool": tool.name, "args": args})
        verdict = decision.get("verdict") if isinstance(decision, dict) else None
        if verdict not in ("approve", "edit"):
            return f"NOT EXECUTED: the human decision was {verdict or 'missing'}."
        final_args = decision.get("args") if verdict == "edit" else args
        try:
            final_args = validate_write_args(tool.name, final_args, write_schemas)
        except InvalidAction as exc:
            return f"NOT EXECUTED: {exc}"
        text, _ = await call_tool(tool, final_args, node="baseline", call_id=f"baseline:{tool.name}")
        return text

    assert tool.args_schema is not None, "MCP tools always carry an input schema"
    return StructuredTool(name=tool.name, description=tool.description, args_schema=tool.args_schema, coroutine=gated)


def build_baseline(
    model: BaseChatModel, toolbox: Toolbox, checkpointer: BaseCheckpointSaver[Any]
) -> CompiledStateGraph[Any, Any, Any, Any]:
    write_schemas = {name: dict(t.args_schema) for name, t in toolbox.write_tools.items()}  # type: ignore[arg-type]
    tools = [*toolbox.read_tools.values(), *(approval_gated(t, write_schemas) for t in toolbox.write_tools.values())]
    return create_react_agent(model, tools, prompt=BASELINE_PROMPT, checkpointer=checkpointer)
