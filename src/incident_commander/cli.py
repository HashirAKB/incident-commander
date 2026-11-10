"""ic: the command-line interface. Every command is a thin wrapper over runner.py."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from incident_commander import runner, timetravel, tracing
from incident_commander.approvals import DecisionRefused
from incident_commander.config import Settings, load_settings
from incident_commander.state import Limits

app = typer.Typer(help="LangGraph incident commander for a synthetic IoT fleet.", no_args_is_help=True)
T = TypeVar("T")


def run_async[T](make: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(make())  # type: ignore[arg-type]


def _limits(settings: Settings, steps: int | None, tokens: int | None, cost: float | None) -> Limits:
    limits = runner.default_limits(settings)
    if steps is not None:
        limits["max_steps"] = steps
    if tokens is not None:
        limits["max_tokens"] = tokens
    if cost is not None:
        limits["max_cost_usd"] = cost
    return limits


async def _print_event(event: dict[str, Any]) -> None:
    if event["type"] == "node":
        typer.echo(f"  [node] {event['node']}")
    elif event["type"] == "progress":
        typer.echo(f"  [progress] {event.get('investigator')}: {event.get('tool')}")
    elif event["type"] == "approval_requested":
        typer.echo(f"  [paused] waiting for approval {event['approval_request']['approval_id']}")


def _print_status(status: runner.RunStatus) -> None:
    values = status.values
    typer.echo(f"thread_id:   {status.thread_id}")
    typer.echo(f"phase:       {status.phase}")
    typer.echo(f"mode:        {values.get('mode', '-')}")
    if status.stop_reason:
        typer.echo(f"stop_reason: {status.stop_reason}")
    if values.get("diagnosis"):
        diagnosis = values["diagnosis"]
        typer.echo(f"diagnosis:   {diagnosis['root_cause']} ({diagnosis['confidence']:.2f}) {diagnosis['summary']}")
    request = status.approval_request
    if request:
        typer.echo(f"approval:    {request['approval_id']} expires {request['expires_at']}")
        for action in request["actions"]:
            args = json.dumps(action["args"], sort_keys=True)
            typer.echo(f"  {action['action_id']} {action['tool']} {args}  (risk: {action['risk']})")
    if values.get("postmortem_path"):
        typer.echo(f"postmortem:  {values['postmortem_path']}")


@app.command()
def run(
    alert: Annotated[Path, typer.Option(help="Alert JSON file", exists=True, dir_okay=False)],
    max_steps: Annotated[int | None, typer.Option(help="Step limit (model + tool calls)")] = None,
    max_tokens: Annotated[int | None, typer.Option(help="Token limit")] = None,
    max_cost: Annotated[float | None, typer.Option(help="Cost limit in USD")] = None,
    thread_id: Annotated[str | None, typer.Option(help="Use this thread id (UUID) instead of a new one")] = None,
    follow: Annotated[
        bool, typer.Option(help="After pausing, keep watching until someone else finishes the run")
    ] = False,
) -> None:
    """Start a run for an alert; it stops at completion or when it needs approval."""
    settings = load_settings()

    async def go() -> runner.RunStatus:
        async with runner.open_runtime(settings) as rt:
            limits = _limits(settings, max_steps, max_tokens, max_cost)
            status = await runner.start_run(
                rt, runner.load_alert(alert), thread_id=thread_id, limits=limits, on_event=_print_event
            )
            _print_status(status)
            while follow and status.phase == "paused":
                # The approval may come from another terminal or the API. Everything we
                # need is in the checkpointer, so this process can die at any time.
                await asyncio.sleep(1)
                status = await runner.get_status(rt, status.thread_id)
            return status

    final = run_async(go)
    if follow:
        _print_status(final)


@app.command()
def status(thread_id: str) -> None:
    """Show where a run is: paused, finished or interrupted."""
    settings = load_settings()

    async def go() -> runner.RunStatus:
        async with runner.open_runtime(settings) as rt:
            return await runner.get_status(rt, thread_id)

    result = run_async(go)
    if result.phase == "not_found":
        typer.echo(f"no run with thread_id {thread_id}", err=True)
        raise typer.Exit(1)
    _print_status(result)


@app.command()
def resume(thread_id: str) -> None:
    """Continue a run whose process died mid-step (phase: interrupted)."""
    settings = load_settings()

    async def go() -> runner.RunStatus:
        async with runner.open_runtime(settings) as rt:
            return await runner.resume_run(rt, thread_id, on_event=_print_event)

    _print_status(run_async(go))


@app.command()
def pending() -> None:
    """List runs waiting for approval."""
    settings = load_settings()

    async def go() -> list[runner.RunStatus]:
        async with runner.open_runtime(settings) as rt:
            return await runner.list_pending(rt)

    runs = run_async(go)
    if not runs:
        typer.echo("no runs waiting for approval")
    for item in runs:
        request = item.approval_request or {}
        alert = item.values["alert"]
        typer.echo(
            f"{item.thread_id}  {alert['alert_id']}  {len(request.get('actions', []))} action(s)  "
            f"expires {request.get('expires_at')}"
        )


def _ask_decisions(request: dict[str, Any]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for action in request["actions"]:
        typer.echo(f"\n{action['action_id']} {action['tool']} {json.dumps(action['args'], sort_keys=True)}")
        typer.echo(f"  rationale: {action['rationale']}\n  risk: {action['risk']}")
        choice = typer.prompt("  [a]pprove / [e]dit / [r]eject", default="r").strip().lower()
        if choice.startswith("a"):
            answers[action["action_id"]] = {"verdict": "approve", "args_hash": action["args_hash"]}
        elif choice.startswith("e"):
            edited = typer.prompt("  new args (JSON)", default=json.dumps(action["args"], sort_keys=True))
            answers[action["action_id"]] = {
                "verdict": "edit",
                "args_hash": action["args_hash"],
                "args": json.loads(edited),
            }
        else:
            answers[action["action_id"]] = {"verdict": "reject"}
    return answers


def _fill_hashes(request: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """A decisions file may omit args_hash; it then binds to the request shown in this session."""
    hashes = {a["action_id"]: a["args_hash"] for a in request["actions"]}
    return {aid: {"args_hash": hashes.get(aid, "")} | answer for aid, answer in answers.items()}


@app.command()
def approve(
    thread_id: str,
    decisions_file: Annotated[
        Path | None, typer.Option(help='JSON {"A1": {"verdict": "approve"}, ...}; omit to answer interactively')
    ] = None,
) -> None:
    """Approve, edit or reject each pending action of a paused run, then resume it."""
    settings = load_settings()

    async def go() -> runner.RunStatus:
        async with runner.open_runtime(settings) as rt:
            status = await runner.get_status(rt, thread_id)
            request = status.approval_request
            if request is None:
                raise DecisionRefused(f"run {thread_id} is {status.phase}, not waiting for approval")
            typer.echo(f"approval {request['approval_id']} expires {request['expires_at']}")
            if decisions_file:
                answers = _fill_hashes(request, json.loads(decisions_file.read_text(encoding="utf-8")))
            else:
                answers = _ask_decisions(request)
            return await runner.submit_decisions(rt, thread_id, request["approval_id"], answers, on_event=_print_event)

    try:
        _print_status(run_async(go))
    except (DecisionRefused, runner.RunNotFound) as exc:
        typer.echo(f"refused: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command("sweep-expired")
def sweep_expired() -> None:
    """Expire every paused run whose approval deadline has passed (expiry = reject)."""
    settings = load_settings()

    async def go() -> list[str]:
        async with runner.open_runtime(settings) as rt:
            return await runner.sweep_expired(rt)

    expired = run_async(go)
    typer.echo(f"expired {len(expired)} run(s)")
    for thread_id in expired:
        typer.echo(f"  {thread_id}")


@app.command()
def history(thread_id: str) -> None:
    """List a run's checkpoints, oldest first: step, checkpoint id, next node, state summary."""
    settings = load_settings()

    async def go() -> list[timetravel.HistoryEntry]:
        async with runner.open_runtime(settings) as rt:
            return await timetravel.history(rt, thread_id)

    for entry in run_async(go):
        next_nodes = ",".join(entry.next_nodes) or "END"
        typer.echo(f"step {entry.step:>3}  {entry.checkpoint_id}  next={next_nodes:<16} {entry.summary}")


@app.command()
def replay(thread_id: str, checkpoint: Annotated[str, typer.Option(help="Checkpoint id from ic history")]) -> None:
    """Re-run a run from an earlier checkpoint. Writes already in the ledger are skipped."""
    settings = load_settings()

    async def go() -> runner.RunStatus:
        async with runner.open_runtime(settings) as rt:
            return await timetravel.replay(rt, thread_id, checkpoint, on_event=_print_event)

    _print_status(run_async(go))


@app.command()
def fork(
    thread_id: str,
    checkpoint: Annotated[str, typer.Option(help="Checkpoint id from ic history")],
    set_values: Annotated[
        list[str], typer.Option("--set", help="key=value, dotted keys allowed, e.g. findings.metrics.summary=...")
    ],
) -> None:
    """Change state at a checkpoint and continue from there on a new branch."""
    settings = load_settings()

    async def go() -> runner.RunStatus:
        async with runner.open_runtime(settings) as rt:
            return await timetravel.fork(rt, thread_id, checkpoint, set_values, on_event=_print_event)

    _print_status(run_async(go))


@app.command()
def trace(thread_id: str) -> None:
    """Print the span timeline of a run from traces/<thread_id>.jsonl."""
    settings = load_settings()
    lines = tracing.format_timeline(tracing.read_trace(settings.traces_dir, thread_id))
    if not lines:
        typer.echo(f"no trace for {thread_id} in {settings.traces_dir}", err=True)
        raise typer.Exit(1)
    for line in lines:
        typer.echo(line)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address; anything but localhost needs APPROVER_TOKEN")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port")] = 8000,
) -> None:
    """Run the HTTP API and the approval page (http://127.0.0.1:8000/)."""
    import uvicorn

    from incident_commander.api import check_bind, create_app

    settings = load_settings()
    check_bind(host, settings.approver_token)
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
