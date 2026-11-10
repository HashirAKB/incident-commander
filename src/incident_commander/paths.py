"""Filesystem paths derived from untrusted input.

thread_id arrives from the CLI and HTTP API. It becomes part of file paths
(runs/<thread_id>/postmortem.md, traces/<thread_id>.jsonl), so it must be a
canonical UUID before it touches the filesystem: "../../etc" never parses.
"""

from __future__ import annotations

import uuid
from pathlib import Path


class InvalidThreadId(ValueError):
    pass


def validate_thread_id(thread_id: str) -> str:
    try:
        parsed = uuid.UUID(thread_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidThreadId(f"thread_id must be a UUID, got {thread_id!r}") from exc
    if str(parsed) != thread_id:
        # Reject braces, uppercase or urn: forms so one run has exactly one path.
        raise InvalidThreadId(f"thread_id must be a canonical lowercase UUID, got {thread_id!r}")
    return thread_id


def run_dir(runs_root: Path, thread_id: str) -> Path:
    return runs_root / validate_thread_id(thread_id)
