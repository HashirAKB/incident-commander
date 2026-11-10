"""HTTP API: start runs, watch them over Server-Sent Events, approve actions.

    POST /runs                      start a run (bearer token)       -> 202 {thread_id}
    GET  /runs/pending              runs waiting for approval
    GET  /runs/{thread_id}          current state from the checkpointer
    GET  /runs/{thread_id}/events   SSE: snapshot first, then live events
    POST /runs/{thread_id}/decisions approve/edit/reject (bearer token) -> 202, or 409 if refused
    GET  /                          the approval page (static HTML)

Runs execute as background asyncio tasks in this process. All durable state is
in the checkpointer, so killing the server loses only in-flight steps; a paused
run is resumed by whichever server process receives the decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.types import Command

from incident_commander import runner
from incident_commander.approvals import DecisionRefused
from incident_commander.config import Settings
from incident_commander.paths import InvalidThreadId, validate_thread_id
from incident_commander.state import Limits
from incident_commander.timeutil import Clock, system_clock

log = logging.getLogger("incident_commander.api")
STATIC_DIR = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_BUFFERED_EVENTS = 500


def check_bind(host: str, approver_token: str | None) -> None:
    """Refuse to expose approvals beyond this machine without a token."""
    if host not in LOCAL_HOSTS and not approver_token:
        raise SystemExit(f"refusing to bind {host}: set APPROVER_TOKEN before serving beyond 127.0.0.1")


class EventLog:
    """Events of each thread's current drive: a replay buffer plus live subscriber queues."""

    def __init__(self) -> None:
        self.buffers: dict[str, list[dict[str, Any]]] = {}
        self.subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def reset(self, thread_id: str) -> None:
        self.buffers[thread_id] = []

    async def publish(self, thread_id: str, event: dict[str, Any]) -> None:
        buffer = self.buffers.setdefault(thread_id, [])
        if len(buffer) < MAX_BUFFERED_EVENTS:
            buffer.append(event)
        for queue in self.subscribers.get(thread_id, set()):
            queue.put_nowait(event)

    def subscribe(self, thread_id: str) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any]]]:
        # No await between copying the buffer and registering the queue, so no
        # event can slip between them (asyncio runs one coroutine at a time).
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.subscribers.setdefault(thread_id, set()).add(queue)
        return list(self.buffers.get(thread_id, [])), queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.get(thread_id, set()).discard(queue)


class RunManager:
    """Owns the background tasks that drive runs in this process."""

    def __init__(self, rt: runner.Runtime) -> None:
        self.rt = rt
        self.events = EventLog()
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def busy(self, thread_id: str) -> bool:
        return thread_id in self.tasks

    def _launch(self, thread_id: str, work: Callable[[runner.EventSink], Awaitable[runner.RunStatus]]) -> None:
        self.events.reset(thread_id)

        async def publish(event: dict[str, Any]) -> None:
            await self.events.publish(thread_id, event)

        async def background() -> None:
            try:
                status = await work(publish)
                await publish({"type": "end", "phase": status.phase, "stop_reason": status.stop_reason})
            except Exception as exc:  # recorded in the run by runner.drive; reported, not swallowed
                log.exception("run %s failed", thread_id)
                await publish({"type": "end", "phase": "finished", "stop_reason": "error", "error": str(exc)})
            finally:
                self.tasks.pop(thread_id, None)

        self.tasks[thread_id] = asyncio.create_task(background())

    def start(self, alert: dict[str, Any], limits: Limits | None) -> str:
        parsed = runner.parse_alert(alert)
        thread_id = str(uuid.uuid4())
        self._launch(
            thread_id, lambda sink: runner.start_run(self.rt, parsed, thread_id=thread_id, limits=limits, on_event=sink)
        )
        return thread_id

    async def decide(self, thread_id: str, approval_id: str, answers: dict[str, Any]) -> None:
        """Validate now (so refusals are synchronous 409s), then resume in the background."""
        if self.busy(thread_id):
            raise DecisionRefused("run is busy: a previous decision is still being applied")
        command = await runner.decision_command(self.rt, thread_id, approval_id, answers)
        if self.busy(thread_id):  # re-check after the await above
            raise DecisionRefused("run is busy: a previous decision is still being applied")
        self._resume(thread_id, command)

    def _resume(self, thread_id: str, command: Command[Any]) -> None:
        self._launch(thread_id, lambda sink: runner.drive(self.rt, command, thread_id, sink))

    async def sweep_expired(self) -> list[str]:
        expired = []
        for status in await runner.list_pending(self.rt):
            if runner.is_expired(self.rt, status) and not self.busy(status.thread_id):
                self._resume(status.thread_id, await runner.expiry_command(self.rt, status.thread_id))
                expired.append(status.thread_id)
        return expired

    async def wait_idle(self) -> None:
        while self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def public_view(status: runner.RunStatus, busy: bool) -> dict[str, Any]:
    """What the API returns about a run. No secrets live in state, so this is a projection for size."""
    values = status.values
    return {
        "thread_id": status.thread_id,
        "phase": "running" if busy else status.phase,
        "mode": values.get("mode"),
        "alert": values.get("alert"),
        "triage": values.get("triage"),
        "findings": values.get("findings", {}),
        "diagnosis": values.get("diagnosis"),
        "approval_request": status.approval_request,
        "dropped_proposals": values.get("dropped_proposals", []),
        "decisions": values.get("decisions", {}),
        "executions": values.get("executions", []),
        "usage": values.get("usage"),
        "stop_reason": values.get("stop_reason"),
    }


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, sort_keys=True, default=str)}\n\n"


def create_app(
    settings: Settings,
    *,
    clock: Clock = system_clock,
    sweep_interval_seconds: float = 60,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with runner.open_runtime(settings, clock=clock) as rt:
            manager = RunManager(rt)
            app.state.manager = manager

            async def sweeper() -> None:
                while True:
                    await asyncio.sleep(sweep_interval_seconds)
                    try:
                        await manager.sweep_expired()
                    except Exception:
                        log.exception("expiry sweep failed; retrying next interval")

            sweep_task = asyncio.create_task(sweeper())
            try:
                yield
            finally:
                sweep_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweep_task
                await manager.wait_idle()

    app = FastAPI(title="incident-commander", lifespan=lifespan)

    def manager_of(request: Request) -> RunManager:
        manager: RunManager = request.app.state.manager
        return manager

    def require_token(authorization: str | None = Header(default=None)) -> None:
        expected = settings.approver_token
        if not expected:
            raise HTTPException(503, "approvals are disabled: set APPROVER_TOKEN")
        given = (authorization or "").removeprefix("Bearer ").strip()
        # compare_digest takes the same time whatever the input, so the token
        # cannot be guessed one character at a time from response timings.
        if not hmac.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(401, "missing or wrong bearer token")

    def thread_or_400(thread_id: str) -> str:
        try:
            return validate_thread_id(thread_id)
        except InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc

    async def status_or_404(manager: RunManager, thread_id: str) -> runner.RunStatus:
        status = await runner.get_status(manager.rt, thread_or_400(thread_id))
        if status.phase == "not_found" and not manager.busy(thread_id):
            raise HTTPException(404, "no such run")
        return status

    @app.get("/")
    async def approval_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "approve.html", media_type="text/html")

    @app.post("/runs", status_code=202, dependencies=[Depends(require_token)])
    async def create_run(body: dict[str, Any], request: Request) -> dict[str, str]:
        try:
            thread_id = manager_of(request).start(body.get("alert", {}), body.get("limits"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"thread_id": thread_id}

    @app.get("/runs/pending")
    async def pending_runs(request: Request) -> list[dict[str, Any]]:
        manager = manager_of(request)
        return [public_view(s, manager.busy(s.thread_id)) for s in await runner.list_pending(manager.rt)]

    @app.get("/runs/{thread_id}")
    async def get_run(thread_id: str, request: Request) -> dict[str, Any]:
        manager = manager_of(request)
        status = await status_or_404(manager, thread_id)
        return public_view(status, manager.busy(thread_id))

    @app.post("/runs/{thread_id}/decisions", status_code=202, dependencies=[Depends(require_token)])
    async def post_decisions(thread_id: str, body: dict[str, Any], request: Request) -> dict[str, str]:
        manager = manager_of(request)
        await status_or_404(manager, thread_id)
        try:
            await manager.decide(thread_id, str(body.get("approval_id", "")), body.get("decisions") or {})
        except DecisionRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "accepted"}

    @app.get("/runs/{thread_id}/events")
    async def run_events(thread_id: str, request: Request) -> StreamingResponse:
        manager = manager_of(request)
        await status_or_404(manager, thread_id)
        buffered, queue = manager.events.subscribe(thread_id)

        async def stream() -> AsyncIterator[str]:
            try:
                # Late subscribers first get the state from the checkpointer.
                status = await runner.get_status(manager.rt, thread_id)
                yield sse("snapshot", public_view(status, manager.busy(thread_id)))
                for event in buffered:
                    yield sse(event["type"], event)
                if any(e["type"] == "end" for e in buffered) or not manager.busy(thread_id):
                    if not any(e["type"] == "end" for e in buffered):
                        yield sse("end", {"type": "end", "phase": status.phase, "stop_reason": status.stop_reason})
                    return
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield sse(event["type"], event)
                    if event["type"] == "end":
                        return
            finally:
                manager.events.unsubscribe(thread_id, queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app
