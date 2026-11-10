"""Graph nodes. Each module exposes a `make_<node>(...)` factory.

Factories receive their dependencies (model, tools, settings) explicitly, so
you can see exactly what each node can touch. Only executor.py receives
WRITE tools.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig

from incident_commander.state import IncidentState


class StateNode(Protocol):
    """A node function. LangGraph matches nodes by the parameter name `state`."""

    def __call__(self, state: IncidentState) -> Awaitable[dict[str, Any]]: ...


class StateConfigNode(Protocol):
    """A node that also reads the run config (for the thread_id)."""

    def __call__(self, state: IncidentState, config: RunnableConfig) -> Awaitable[dict[str, Any]]: ...
