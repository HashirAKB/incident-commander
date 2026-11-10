"""Time travel: list a run's checkpoints, replay from one, or fork with changed state.

Every step of a run is a checkpoint. Passing a `checkpoint_id` in the config
makes LangGraph start from that snapshot instead of the latest one:

- replay: continue from an old checkpoint as is (the nodes after it run again);
- fork:   first write changed values at that checkpoint (aupdate_state creates
          a new checkpoint whose parent is the old one), then continue.

Both stay on the same thread_id, so the write ledger still applies: an action
that already ran is skipped, and any new action needs a new approval because
prepare_approval and approval run again on the new branch.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import StateSnapshot

from incident_commander.runner import EventSink, RunStatus, Runtime, config_for, drive


@dataclass(frozen=True)
class HistoryEntry:
    checkpoint_id: str
    step: int
    next_nodes: tuple[str, ...]
    summary: str


def summarize(values: dict[str, Any]) -> str:
    """One line describing what the state holds at a checkpoint."""
    parts = []
    if "triage" in values:
        parts.append(f"triage={values['triage']['category']}")
    if values.get("findings"):
        parts.append(f"findings={','.join(values['findings'])}")
    if "diagnosis" in values:
        parts.append(f"diagnosis={values['diagnosis']['root_cause']}")
    if values.get("proposed_actions"):
        parts.append(f"actions={len(values['proposed_actions'])}")
    if values.get("decisions"):
        parts.append("decisions=" + ",".join(f"{k}:{d['verdict']}" for k, d in values["decisions"].items()))
    if values.get("executions"):
        parts.append("executed=" + ",".join(f"{e['action_id']}:{e['status']}" for e in values["executions"]))
    if "stop_reason" in values:
        parts.append(f"stop_reason={values['stop_reason']}")
    return " ".join(parts) or "input only"


def _entry(snapshot: StateSnapshot) -> HistoryEntry:
    return HistoryEntry(
        checkpoint_id=str(snapshot.config["configurable"]["checkpoint_id"]),
        step=int(snapshot.metadata.get("step", -1)) if snapshot.metadata else -1,
        next_nodes=tuple(snapshot.next),
        summary=summarize(dict(snapshot.values)),
    )


async def history(rt: Runtime, thread_id: str) -> list[HistoryEntry]:
    """Checkpoints of a thread, oldest first (all branches, as stored)."""
    snapshots = [s async for s in rt.graph.aget_state_history(config_for(thread_id))]
    return [_entry(s) for s in reversed(snapshots)]


def at_checkpoint(thread_id: str, checkpoint_id: str) -> RunnableConfig:
    config = config_for(thread_id)
    config["configurable"]["checkpoint_id"] = checkpoint_id
    config["configurable"]["checkpoint_ns"] = ""  # "" = the top-level graph (subgraphs have their own)
    return config


async def _snapshot(rt: Runtime, thread_id: str, checkpoint_id: str) -> StateSnapshot:
    snapshot = await rt.graph.aget_state(at_checkpoint(thread_id, checkpoint_id))
    if not snapshot.values:
        raise LookupError(f"no checkpoint {checkpoint_id} on thread {thread_id}")
    return snapshot


async def replay(rt: Runtime, thread_id: str, checkpoint_id: str, on_event: EventSink | None = None) -> RunStatus:
    await _snapshot(rt, thread_id, checkpoint_id)
    return await drive(rt, None, thread_id, on_event, config=at_checkpoint(thread_id, checkpoint_id))


def parse_assignment(assignment: str) -> tuple[list[str], Any]:
    """'findings.metrics.summary=text' -> (['findings', 'metrics', 'summary'], 'text'). JSON values allowed."""
    key, sep, raw = assignment.partition("=")
    if not sep or not key:
        raise ValueError(f"expected key=value, got {assignment!r}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return key.split("."), value


# Only fields an investigator or the synthesizer produces may be changed in a
# fork. Inputs (alert, limits, mode) and approval state (approval_request,
# decisions, executions) are off limits: a fork must never fake an approval.
FORKABLE_FIELDS = {"triage", "findings", "diagnosis"}


async def fork(
    rt: Runtime,
    thread_id: str,
    checkpoint_id: str,
    assignments: list[str],
    on_event: EventSink | None = None,
) -> RunStatus:
    snapshot = await _snapshot(rt, thread_id, checkpoint_id)
    updates: dict[str, Any] = {}
    for assignment in assignments:
        path, value = parse_assignment(assignment)
        top = path[0]
        if top not in FORKABLE_FIELDS:
            raise ValueError(f"cannot fork with a changed {top!r}; allowed: {sorted(FORKABLE_FIELDS)}")
        if top not in updates:
            updates[top] = copy.deepcopy(snapshot.values.get(top))
        if len(path) == 1:
            updates[top] = value
            continue
        target = updates[top]
        for part in path[1:-1]:
            target = target[part]
        target[path[-1]] = value
    # The new checkpoint's parent is `checkpoint_id`: history now has a branch.
    forked = await rt.graph.aupdate_state(at_checkpoint(thread_id, checkpoint_id), updates)
    return await drive(rt, None, thread_id, on_event, config=forked)
