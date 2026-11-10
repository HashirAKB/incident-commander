"""The write ledger: which (thread_id, action_id) pairs were already executed.

The executor checks the ledger before every write and records the result right
after the tool returns. If the process dies after the write but before the
graph checkpoints, the resumed executor finds the ledger row and skips the
action instead of restarting the device twice.

This is at-least-once delivery with dedupe, not exactly-once: if the process
dies after the MCP server performed the write but before the ledger row is
committed, the action runs again on resume. The window is one local database
insert. Closing it would need the device API to accept an idempotency key
(see docs/adr/0004-ledger-dedupe.md).

Storage: a SQLite file next to the checkpoints, or a table in Postgres when
DATABASE_URL is set.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from incident_commander.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS write_ledger (
    thread_id   TEXT NOT NULL,
    action_id   TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args_hash   TEXT NOT NULL,
    status      TEXT NOT NULL,
    result      TEXT NOT NULL,
    PRIMARY KEY (thread_id, action_id)
)
"""


@dataclass(frozen=True)
class LedgerEntry:
    thread_id: str
    action_id: str
    tool: str
    args_hash: str
    status: str  # executed | failed
    result: str


class Ledger:
    """Small blocking store; every call runs in a worker thread so the event loop stays free."""

    def __init__(self, settings: Settings) -> None:
        self.database_url = settings.database_url
        self.sqlite_path: Path = settings.data_dir / "ledger.sqlite"
        self._run_sync(self._create)

    # ------------------------------------------------------------ connection

    def _connect(self) -> Any:
        if self.database_url:
            import psycopg

            return psycopg.connect(self.database_url, autocommit=True)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.sqlite_path, isolation_level=None)  # autocommit

    def _sql(self, query: str) -> str:
        return query.replace("?", "%s") if self.database_url else query

    def _run_sync(self, fn: Any, *args: Any) -> Any:
        connection = self._connect()
        try:
            return fn(connection, *args)
        finally:
            connection.close()

    # --------------------------------------------------------------- queries

    def _create(self, connection: Any) -> None:
        connection.execute(SCHEMA)

    def _get(self, connection: Any, thread_id: str, action_id: str) -> LedgerEntry | None:
        row = connection.execute(
            self._sql(
                "SELECT thread_id, action_id, tool, args_hash, status, result FROM write_ledger "
                "WHERE thread_id = ? AND action_id = ?"
            ),
            (thread_id, action_id),
        ).fetchone()
        return LedgerEntry(*row) if row else None

    def _record(self, connection: Any, entry: LedgerEntry) -> None:
        # ON CONFLICT DO NOTHING: the first record wins; a duplicate can never overwrite it.
        connection.execute(
            self._sql(
                "INSERT INTO write_ledger (thread_id, action_id, tool, args_hash, status, result) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (thread_id, action_id) DO NOTHING"
            ),
            (entry.thread_id, entry.action_id, entry.tool, entry.args_hash, entry.status, entry.result),
        )

    def _for_thread(self, connection: Any, thread_id: str) -> list[LedgerEntry]:
        rows = connection.execute(
            self._sql(
                "SELECT thread_id, action_id, tool, args_hash, status, result FROM write_ledger "
                "WHERE thread_id = ? ORDER BY action_id"
            ),
            (thread_id,),
        ).fetchall()
        return [LedgerEntry(*row) for row in rows]

    # ------------------------------------------------------------ public API

    async def get(self, thread_id: str, action_id: str) -> LedgerEntry | None:
        result: LedgerEntry | None = await asyncio.to_thread(self._run_sync, self._get, thread_id, action_id)
        return result

    async def record(self, entry: LedgerEntry) -> None:
        await asyncio.to_thread(self._run_sync, self._record, entry)

    async def for_thread(self, thread_id: str) -> list[LedgerEntry]:
        result: list[LedgerEntry] = await asyncio.to_thread(self._run_sync, self._for_thread, thread_id)
        return result
