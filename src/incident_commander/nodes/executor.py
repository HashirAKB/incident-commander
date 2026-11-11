"""executor: the ONLY node that holds WRITE tools.

It runs an action only if the approval node recorded an approve/edit decision
for it, re-validates the final args against the tool schema, and goes through
the ledger so a resumed or replayed run never repeats a write.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from incident_commander import tracing
from incident_commander.actions import InvalidAction, args_hash, validate_write_args
from incident_commander.ledger import Ledger, LedgerEntry
from incident_commander.llm import call_tool
from incident_commander.nodes import StateConfigNode
from incident_commander.state import EMPTY_USAGE, Execution, IncidentState, Usage, sum_usage


def make_executor(write_tools: dict[str, BaseTool], ledger: Ledger) -> StateConfigNode:
    write_schemas = {name: dict(tool.args_schema) for name, tool in write_tools.items()}  # type: ignore[arg-type]

    async def executor(state: IncidentState, config: RunnableConfig) -> dict[str, Any]:
        with tracing.node_span("executor"):
            thread_id = str(config["configurable"]["thread_id"])
            decisions = state.get("decisions", {})
            # What the human was shown: action id -> (tool, hash of the proposed args).
            shown = {
                a["action_id"]: (a["tool"], a["args_hash"])
                for a in state.get("approval_request", {}).get("actions", [])
            }
            executions: list[Execution] = []
            usage: Usage = EMPTY_USAGE
            for action in state.get("proposed_actions", []):
                decision = decisions.get(action["action_id"])
                if decision is None or decision["verdict"] not in ("approve", "edit") or decision["args"] is None:
                    continue  # no approval, no write
                if shown.get(action["action_id"]) != (action["tool"], args_hash(action["tool"], action["args"])):
                    # The proposal changed after the human saw it (e.g. a manual state edit):
                    # the approval does not cover it.
                    executions.append(
                        _execution(
                            action["action_id"], action["tool"], action["args"], "failed", "not the approved action"
                        )
                    )
                    continue
                try:
                    args = validate_write_args(action["tool"], decision["args"], write_schemas)
                except InvalidAction as exc:
                    executions.append(
                        _execution(action["action_id"], action["tool"], decision["args"], "failed", str(exc))
                    )
                    continue
                key = args_hash(action["tool"], args)
                previous = await ledger.get(thread_id, action["action_id"], key)
                if previous is not None:
                    executions.append(
                        _execution(action["action_id"], action["tool"], args, "skipped_duplicate", previous.result)
                    )
                    continue
                text, is_error = await call_tool(
                    write_tools[action["tool"]], args, node="executor", call_id=f"{thread_id}:{action['action_id']}"
                )
                status = "failed" if is_error else "executed"
                # Recorded right after the write returns. The gap between the two
                # lines is the at-least-once window described in ledger.py.
                await ledger.record(LedgerEntry(thread_id, action["action_id"], action["tool"], key, status, text))
                executions.append(_execution(action["action_id"], action["tool"], args, status, text))
                usage = sum_usage(usage, {"steps": 1, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            return {"executions": executions, "usage": usage}

    return executor


def _execution(action_id: str, tool: str, args: dict[str, Any], status: Any, result: str) -> Execution:
    return {"action_id": action_id, "tool": tool, "args": args, "status": status, "result": result[:2000]}
