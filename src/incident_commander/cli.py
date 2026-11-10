"""ic: the command-line interface. Every command is a thin wrapper over runner.py."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from incident_commander import runner
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
) -> None:
    """Start a run for an alert; it stops at completion or when it needs approval."""
    settings = load_settings()

    async def go() -> runner.RunStatus:
        async with runner.open_runtime(settings) as rt:
            limits = _limits(settings, max_steps, max_tokens, max_cost)
            return await runner.start_run(
                rt, runner.load_alert(alert), thread_id=thread_id, limits=limits, on_event=_print_event
            )

    _print_status(run_async(go))


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


if __name__ == "__main__":
    app()
