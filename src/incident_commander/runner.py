"""Start, resume and inspect runs. The CLI, the HTTP API and the evals all go through here.

A run is identified by its thread_id. Everything about it lives in the
checkpointer, so any process that opens the same checkpointer can pick it up:
that is what makes "pause for approval, die, resume elsewhere" work.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from incident_commander import tracing
from incident_commander.checkpointer import get_checkpointer
from incident_commander.config import Settings
from incident_commander.graph import RECURSION_LIMIT, GraphDeps, build_graph
from incident_commander.mcp_bridge import open_toolbox
from incident_commander.models.factory import get_chat_model
from incident_commander.nodes.postmortem import render_postmortem, write_postmortem
from incident_commander.paths import validate_thread_id
from incident_commander.registry import Toolbox
from incident_commander.state import EMPTY_USAGE, Alert, IncidentState, Limits
from incident_commander.timeutil import Clock, parse_iso, system_clock, to_iso

RunPhase = Literal["paused", "finished", "interrupted", "not_found"]
EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class RunNotFound(LookupError):
    pass


@dataclass
class Runtime:
    """Everything a run needs, opened once per process."""

    settings: Settings
    graph: CompiledStateGraph[Any, Any, Any, Any]
    toolbox: Toolbox
    clock: Clock = system_clock


@dataclass
class RunStatus:
    thread_id: str
    phase: RunPhase
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def stop_reason(self) -> str | None:
        return self.values.get("stop_reason")

    @property
    def approval_request(self) -> dict[str, Any] | None:
        return self.values.get("approval_request") if self.phase == "paused" else None


@asynccontextmanager
async def open_runtime(
    settings: Settings,
    *,
    compromised: bool = False,
    clock: Clock = system_clock,
    model: BaseChatModel | None = None,
) -> AsyncIterator[Runtime]:
    tracing.init_tracing(settings.traces_dir)
    async with get_checkpointer(settings) as checkpointer, open_toolbox(settings) as toolbox:
        deps = GraphDeps(
            model=model or get_chat_model(settings, compromised=compromised),
            toolbox=toolbox,
            runs_dir=settings.runs_dir,
        )
        yield Runtime(settings=settings, graph=build_graph(deps, checkpointer), toolbox=toolbox, clock=clock)


def config_for(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": validate_thread_id(thread_id)}, "recursion_limit": RECURSION_LIMIT}


ALERT_TARGET = re.compile(r"^(D-\d{2}|fleet)$")


def load_alert(path: Path) -> Alert:
    """Read and check an alert file. Alerts are input, so they are validated like any input."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("alert_id", "target", "metric", "summary", "fired_at"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"alert {path} needs a non-empty string field {key!r}")
    if not ALERT_TARGET.match(data["target"]):
        raise ValueError(f"alert target must be a device id like D-17 or 'fleet', got {data['target']!r}")
    parse_iso(data["fired_at"])
    alert: Alert = {k: data[k] for k in ("alert_id", "target", "metric", "summary", "fired_at")}  # type: ignore[assignment]
    if isinstance(data.get("site"), str):
        alert["site"] = data["site"]
    return alert


def default_limits(settings: Settings) -> Limits:
    return {"max_steps": settings.max_steps, "max_tokens": settings.max_tokens, "max_cost_usd": settings.max_cost_usd}


def initial_state(alert: Alert, settings: Settings, started_at: str, limits: Limits) -> IncidentState:
    return {
        "alert": alert,
        "mode": settings.mode,
        "started_at": started_at,
        "limits": limits,
        "approval_ttl_seconds": settings.approval_ttl_seconds,
        "findings": {},
        "usage": EMPTY_USAGE,
    }


async def get_status(rt: Runtime, thread_id: str) -> RunStatus:
    snapshot = await rt.graph.aget_state(config_for(thread_id))
    if not snapshot.values:
        return RunStatus(thread_id, "not_found")
    if snapshot.interrupts:
        phase: RunPhase = "paused"
    elif not snapshot.next:
        phase = "finished"
    else:
        phase = "interrupted"  # the process died mid-step; resume_run continues it
    return RunStatus(thread_id, phase, dict(snapshot.values))


def _event(namespace: tuple[str, ...], mode: str, chunk: Any) -> dict[str, Any] | None:
    """Translate one LangGraph stream item into a small JSON event (CLI progress, SSE)."""
    if mode == "custom":
        return {"type": "progress", **chunk}
    if namespace:  # updates from inside an investigator subgraph: too chatty for the stream
        return None
    for node, update in chunk.items():
        if node == "__interrupt__":
            return {"type": "approval_requested", "approval_request": update[0].value}
        return {"type": "node", "node": node, "update": update or {}}
    return None


async def _drive(rt: Runtime, graph_input: Any, thread_id: str, on_event: EventSink | None) -> RunStatus:
    labels = tracing.current_run.set(tracing.RunLabels(thread_id, rt.settings.mode))
    try:
        stream = rt.graph.astream(
            graph_input,
            config_for(thread_id),
            stream_mode=["updates", "custom"],
            subgraphs=True,
            # "sync": each checkpoint is written before the next step starts, so
            # a crash loses at most the step in flight.
            durability="sync",
        )
        async for namespace, mode, chunk in stream:
            # With subgraphs=True and several stream modes, items are (namespace, mode, chunk).
            event = _event(tuple(namespace), str(mode), chunk)
            if event and on_event:
                await on_event(event)
    except Exception as exc:
        await record_error(rt, thread_id, exc)
        raise
    finally:
        tracing.current_run.reset(labels)
    return await get_status(rt, thread_id)


async def record_error(rt: Runtime, thread_id: str, exc: BaseException) -> None:
    """Mark the run finished with stop_reason=error and still write a postmortem."""
    message = tracing.redact(f"{type(exc).__name__}: {exc}")
    config = config_for(thread_id)
    snapshot = await rt.graph.aget_state(config)
    if not snapshot.values:
        return  # failed before the first checkpoint: nothing to record
    # as_node="postmortem" records the update as if postmortem ran, so the run
    # has no next step and can never be resumed into a half-finished state.
    await rt.graph.aupdate_state(config, {"stop_reason": "error", "error": message}, as_node="postmortem")
    state = cast(IncidentState, (await rt.graph.aget_state(config)).values)
    write_postmortem(rt.settings.runs_dir, thread_id, render_postmortem(state, thread_id, "error"))


async def start_run(
    rt: Runtime,
    alert: Alert,
    *,
    thread_id: str | None = None,
    limits: Limits | None = None,
    on_event: EventSink | None = None,
) -> RunStatus:
    thread_id = validate_thread_id(thread_id or str(uuid.uuid4()))
    state = initial_state(alert, rt.settings, to_iso(rt.clock()), limits or default_limits(rt.settings))
    return await _drive(rt, state, thread_id, on_event)


async def resume_run(rt: Runtime, thread_id: str, on_event: EventSink | None = None) -> RunStatus:
    """Continue a run whose process died mid-step, from its last checkpoint."""
    status = await get_status(rt, thread_id)
    if status.phase != "interrupted":
        raise ValueError(f"run {thread_id} is {status.phase}; only an interrupted run can be resumed this way")
    return await _drive(rt, None, thread_id, on_event)
