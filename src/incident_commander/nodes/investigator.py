"""The investigator: ONE compiled subgraph, used by all four investigators.

The parent graph fans out with Send("investigate", InvestigatorInput). Each
branch runs this subgraph with a different `investigator` name, which picks
its READ-only tool subset from the contract (registry.tools_for_investigator).

Inside, a small explicit loop instead of a prebuilt agent:

    START -> model -> (tools -> model)* -> report -> END

Limits: at most MAX_TOOL_CALLS tool calls, and the branch stops early when it
has spent its share of the run budget (branch_budget).

Input/output schemas keep the branch from writing anything to the parent
except `findings` and `usage`, both of which have reducers, so parallel
branches merge safely.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from incident_commander import budgets, prompts, tracing
from incident_commander.llm import call_tool, context_messages, tool_calling_turn
from incident_commander.registry import tools_for_investigator
from incident_commander.state import EMPTY_USAGE, Finding, InvestigatorInput, Usage, merge_findings, sum_usage

MAX_TOOL_CALLS = 3


def _append(left: list[str], right: list[str]) -> list[str]:
    return [*left, *right]


class InvestigatorState(InvestigatorInput):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_calls_made: Annotated[list[str], _append]
    blocked_tool_calls: Annotated[list[str], _append]
    out_of_budget: bool
    findings: Annotated[dict[str, Finding], merge_findings]
    usage: Annotated[Usage, sum_usage]


class InvestigatorOutput(TypedDict):
    findings: Annotated[dict[str, Finding], merge_findings]
    usage: Annotated[Usage, sum_usage]


def describe_call(name: str, args: dict[str, Any]) -> str:
    return f"{name}({json.dumps(args, sort_keys=True)})"


def build_investigator(model: BaseChatModel, read_tools: dict[str, BaseTool]) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the investigator subgraph. It never receives WRITE tools."""

    def allowed_tools(state: InvestigatorState) -> list[BaseTool]:
        return [read_tools[name] for name in tools_for_investigator(state["investigator"]) if name in read_tools]

    async def model_turn(state: InvestigatorState) -> dict[str, Any]:
        node = f"investigate:{state['investigator']}"
        spent = state.get("usage") or EMPTY_USAGE
        if budgets.exceeded(spent, state["branch_budget"]) is not None:
            return {"out_of_budget": True}
        messages: list[BaseMessage] = list(state.get("messages") or [])
        new_messages: list[BaseMessage] = []
        if not messages:
            context = {
                "alert": state["alert"],
                "category": state["triage"]["category"],
                "investigator": state["investigator"],
            }
            new_messages += context_messages(prompts.investigator_prompt(state["investigator"]), context)
        if len(state.get("tool_calls_made") or []) >= MAX_TOOL_CALLS:
            new_messages.append(HumanMessage(content=prompts.FINAL_ANSWER_NUDGE))
        reply, usage = await tool_calling_turn(model, allowed_tools(state), messages + new_messages, node=node)
        return {"messages": [*new_messages, reply], "usage": usage}

    def after_model(state: InvestigatorState) -> Literal["tools", "report"]:
        last = state["messages"][-1] if state.get("messages") else None
        wants_tools = isinstance(last, AIMessage) and bool(last.tool_calls)
        under_cap = len(state.get("tool_calls_made") or []) < MAX_TOOL_CALLS
        return "tools" if wants_tools and under_cap and not state.get("out_of_budget") else "report"

    async def run_tools(state: InvestigatorState) -> dict[str, Any]:
        node = f"investigate:{state['investigator']}"
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        allowed = {tool.name: tool for tool in allowed_tools(state)}
        write_progress = get_stream_writer()
        results: list[ToolMessage] = []
        made: list[str] = []
        blocked: list[str] = []
        usage = EMPTY_USAGE
        for call in last.tool_calls:
            described = describe_call(call["name"], call["args"])
            call_id = call["id"] or described
            if call["name"] not in allowed or len(made) + len(state.get("tool_calls_made") or []) >= MAX_TOOL_CALLS:
                # Structural refusal: the tool is not bound to this node. A write
                # tool asked for here (prompt injection, hijacked model) ends up
                # as a refusal message, never as a call.
                blocked.append(described)
                text = f"Refused: {call['name']} is not available to the {state['investigator']} investigator."
                results.append(ToolMessage(content=text, tool_call_id=call_id, name=call["name"], status="error"))
                continue
            write_progress({"investigator": state["investigator"], "tool": call["name"]})
            text, is_error = await call_tool(allowed[call["name"]], call["args"], node=node, call_id=call_id)
            status: Literal["success", "error"] = "error" if is_error else "success"
            results.append(ToolMessage(content=text, tool_call_id=call_id, name=call["name"], status=status))
            made.append(described)
            usage = sum_usage(usage, {"steps": 1, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        return {"messages": results, "tool_calls_made": made, "blocked_tool_calls": blocked, "usage": usage}

    async def report(state: InvestigatorState) -> dict[str, Any]:
        name = state["investigator"]
        with tracing.node_span(f"investigate:{name}"):
            last = state["messages"][-1] if state.get("messages") else None
            has_summary = isinstance(last, AIMessage) and not last.tool_calls and bool(last.content)
            summary = str(last.content) if has_summary and last is not None else "(no findings: stopped early)"
            finding: Finding = {
                "finding_id": f"F-{name}",
                "investigator": name,
                "summary": summary,
                "tool_calls": list(state.get("tool_calls_made") or []),
                "blocked_tool_calls": list(state.get("blocked_tool_calls") or []),
                "truncated": bool(state.get("out_of_budget")) or not has_summary,
            }
            return {"findings": {name: finding}}

    graph = StateGraph(InvestigatorState, input_schema=InvestigatorInput, output_schema=InvestigatorOutput)
    graph.add_node("model", model_turn)
    graph.add_node("tools", run_tools)
    graph.add_node("report", report)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, ["tools", "report"])
    graph.add_edge("tools", "model")
    graph.add_edge("report", END)
    return graph.compile()
