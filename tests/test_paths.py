from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from incident_commander.paths import InvalidThreadId, run_dir, validate_thread_id


def test_canonical_uuid_is_accepted() -> None:
    thread_id = str(uuid.uuid4())
    assert run_dir(Path("runs"), thread_id) == Path("runs") / thread_id


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",
        "",
        "not-a-uuid",
        "{12345678-1234-5678-1234-567812345678}",
        "12345678-1234-5678-1234-56781234567A",
    ],
)
def test_traversal_and_non_canonical_ids_are_rejected(bad: str) -> None:
    with pytest.raises(InvalidThreadId):
        validate_thread_id(bad)
