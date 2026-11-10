from __future__ import annotations

import uuid

from incident_commander.config import load_settings
from incident_commander.ledger import Ledger, LedgerEntry


async def test_ledger_records_once_and_first_write_wins(backend: str) -> None:
    ledger = Ledger(load_settings())
    thread_id = str(uuid.uuid4())
    assert await ledger.get(thread_id, "A1") is None
    await ledger.record(LedgerEntry(thread_id, "A1", "restart_device", "h", "executed", "first"))
    await ledger.record(LedgerEntry(thread_id, "A1", "restart_device", "h", "executed", "second"))
    entry = await ledger.get(thread_id, "A1")
    assert entry is not None and entry.result == "first"
    assert [e.action_id for e in await ledger.for_thread(thread_id)] == ["A1"]
