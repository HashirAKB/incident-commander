"""One function chooses where checkpoints live: SQLite locally, Postgres when DATABASE_URL is set.

A checkpoint is a snapshot of the graph state after each step, keyed by
thread_id. Because it is on disk (or in Postgres) and not in memory, a
different process can load it and continue the run after a crash.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver

from incident_commander.config import Settings


@asynccontextmanager
async def get_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
    if settings.database_url:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
            await saver.setup()  # creates tables on first use; idempotent
            yield saver
        return

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.sqlite_path)) as saver:
        yield saver
