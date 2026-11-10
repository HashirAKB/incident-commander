"""Time helpers and the injectable clock.

Nodes never call the clock directly (see nodes/approval.py for why). Callers
outside the graph (runner, sweeper, API) take a `Clock` so tests can move time
forward without sleeping. IC_FAKE_NOW pins the clock for subprocess tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def to_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def system_clock() -> datetime:
    pinned = os.environ.get("IC_FAKE_NOW")
    return parse_iso(pinned) if pinned else datetime.now(UTC)
