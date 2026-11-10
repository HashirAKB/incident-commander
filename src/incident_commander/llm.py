"""Thin helpers every node uses to talk to a model or a tool.

They exist so usage accounting, cost, tracing and output normalization happen
in exactly one place instead of in each node.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from incident_commander import tracing
from incident_commander.mcp_bridge import tool_output_text
from incident_commander.models.fake import FakeChatModel
from incident_commander.pricing import cost_usd, price_for
from incident_commander.state import Usage


def context_messages(system_prompt: str, context: dict[str, Any]) -> list[BaseMessage]:
    """System prompt + one human message holding the node's inputs as JSON.

    sort_keys keeps the prompt byte-identical across runs (deterministic
    mock outputs, and a stable prefix for prompt caching in live mode).
    """
    body = json.dumps(context, indent=2, sort_keys=True)
    return [SystemMessage(content=system_prompt), HumanMessage(content=f"Context:\n```json\n{body}\n```")]


def model_label(model: BaseChatModel) -> tuple[str, str]:
    """(mode, model name) for pricing and trace labels."""
    if isinstance(model, FakeChatModel):
        return "mock", "fake"
    return "live", str(getattr(model, "model", "unknown"))


def usage_of(message: AIMessage, model: BaseChatModel) -> Usage:
    mode, name = model_label(model)
    meta = message.usage_metadata or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    price = price_for(mode, name)  # type: ignore[arg-type]
    return {
        "steps": 1,
        "input_tokens": meta["input_tokens"],
        "output_tokens": meta["output_tokens"],
        "cost_usd": cost_usd(price, meta["input_tokens"], meta["output_tokens"]),
    }


async def structured_call[SchemaT: BaseModel](
    model: BaseChatModel, schema: type[SchemaT], messages: list[BaseMessage], *, node: str
) -> tuple[SchemaT, Usage]:
    """Ask for a structured output; return it with what it cost."""
    mode, _ = model_label(model)
    # Live: Anthropic structured outputs (json_schema). Forced tool_choice, the
    # default "function_calling" method, is rejected by current Claude models.
    kwargs = {"method": "json_schema"} if mode == "live" else {}
    runnable = model.with_structured_output(schema, include_raw=True, **kwargs)
    with tracing.model_span(node=node, mode=mode) as span:
        result = await runnable.ainvoke(messages)
        assert isinstance(result, dict)  # include_raw=True returns {"raw", "parsed", "parsing_error"}
        if result.get("parsing_error") is not None:
            raise ValueError(f"{node}: model output did not match {schema.__name__}: {result['parsing_error']}")
        usage = usage_of(result["raw"], model)
        tracing.record_usage(span, usage)
    parsed = result["parsed"]
    assert isinstance(parsed, schema)
    return parsed, usage


async def tool_calling_turn(
    model: BaseChatModel, tools: list[BaseTool], messages: list[BaseMessage], *, node: str
) -> tuple[AIMessage, Usage]:
    """One model turn with tools bound. The caller decides which tools to bind."""
    mode, _ = model_label(model)
    with tracing.model_span(node=node, mode=mode) as span:
        reply = await model.bind_tools(tools).ainvoke(messages)
        assert isinstance(reply, AIMessage)
        usage = usage_of(reply, model)
        tracing.record_usage(span, usage)
    return reply, usage


async def call_tool(tool: BaseTool, args: dict[str, Any], *, node: str, call_id: str) -> tuple[str, bool]:
    """Call one MCP tool. Returns (text, is_error). Errors are data, not exceptions.

    A tool error (unknown device, bad args) is shown to the model like any
    result, so it can recover; it never crashes the run.
    """
    with tracing.tool_span(node=node, tool=tool.name) as span:
        message = await tool.ainvoke({"name": tool.name, "args": args, "id": call_id, "type": "tool_call"})
        is_error = isinstance(message, ToolMessage) and message.status == "error"
        text = tool_output_text(message)
        span.set_attribute("tool.error", is_error)
    return text, is_error
