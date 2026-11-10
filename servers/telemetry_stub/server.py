"""Python stand-in for the telemetry-ops-copilot MCP server.

Same tool names, argument names and READ/WRITE split as the TypeScript server
(src/tools/definitions.ts at the pinned commit), so the agent cannot tell them
apart. It exists so the project builds, tests and evaluates without Node.

Differences from the real server (documented in docs/mcp-contract.md):
- metrics have 5-minute resolution and a per-device random stream;
- runbook search is keyword overlap, not BM25;
- writes are appended to a JSONL write log (IC_WRITE_LOG) instead of SQLite.

Run: python -m servers.telemetry_stub.server   (stdio transport)
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from servers import seed
from servers.telemetry_stub.anomalies import Method, detect

DeviceId = Annotated[str, Field(pattern=r"^D-\d{2}$", description='Device id, e.g. "D-17"')]
Window = Literal["1h", "6h", "24h", "7d"]
WINDOW_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
METRICS = ("temperature", "vibration", "battery", "signal")
RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "runbooks"

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

mcp = FastMCP("telemetry-ops")


@cache
def _series(device_id: str) -> list[seed.MetricPoint]:
    device = seed.find_device(device_id)
    if device is None:
        raise ValueError(f'Unknown device "{device_id}". Use list_devices to see valid ids.')
    return seed.generate_metrics(device)


def _window(device_id: str, window: Window) -> list[seed.MetricPoint]:
    cutoff = seed.FLEET_NOW - timedelta(hours=WINDOW_HOURS[window])
    return [p for p in _series(device_id) if p.ts > cutoff]


def _value(point: seed.MetricPoint, metric: str) -> float:
    return float(getattr(point, metric))


@mcp.tool(annotations=READ)
def list_devices(
    site: Annotated[str | None, Field(description="Only devices at this site, e.g. plant-a")] = None,
    battery_below: Annotated[float | None, Field(ge=0, le=100, description="Battery below this %")] = None,
) -> list[dict[str, Any]]:
    """List fleet devices with their latest temperature, vibration, battery and signal."""
    rows = []
    for device in seed.all_devices():
        latest = _series(device.id)[-1]
        if site is not None and device.site != site:
            continue
        if battery_below is not None and latest.battery >= battery_below:
            continue
        rows.append(
            {
                "id": device.id,
                "site": device.site,
                "model": device.model,
                "firmware": device.firmware,
                "status": "online",
                "latest": {m: _value(latest, m) for m in METRICS},
            }
        )
    return rows


@mcp.tool(annotations=READ)
def get_metrics(device_id: DeviceId, window: Window = "24h") -> dict[str, Any]:
    """Summary (min/max/avg/latest) and a downsampled series of all metrics for one device."""
    points = _window(device_id, window)
    summary = {}
    for m in METRICS:
        values = [_value(p, m) for p in points]
        summary[m] = {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "avg": round(sum(values) / len(values), 2),
            "latest": values[-1],
        }
    size = math.ceil(len(points) / 12)
    series = [
        {"from": seed.iso(chunk[0].ts)} | {m: round(sum(_value(p, m) for p in chunk) / len(chunk), 2) for m in METRICS}
        for chunk in (points[i : i + size] for i in range(0, len(points), size))
    ]
    return {
        "device_id": device_id,
        "window": window,
        "from": seed.iso(points[0].ts),
        "to": seed.iso(points[-1].ts),
        "points": len(points),
        "summary": summary,
        "series": series,
    }


def _device_anomalies(device_id: str, method: Method, window: Window) -> list[dict[str, Any]]:
    points = _window(device_id, window)
    found = []
    for metric in METRICS:
        for event in detect([_value(p, metric) for p in points], method):
            found.append(
                {
                    "device_id": device_id,
                    "metric": metric,
                    "start": seed.iso(points[event.start_index].ts),
                    "end": seed.iso(points[event.end_index].ts),
                    "peak_value": event.peak_value,
                    "direction": event.direction,
                    "score": event.score,
                    "ongoing": event.end_index == len(points) - 1,
                }
            )
    return found


@mcp.tool(annotations=READ)
def detect_anomalies(
    target: Annotated[str, Field(pattern=r"^(D-\d{2}|fleet)$", description='"D-17" or "fleet"')],
    method: Literal["zscore", "ewma"] = "zscore",
    window: Window = "24h",
) -> dict[str, Any]:
    """Detect anomalies for one device or the whole fleet. zscore: spikes and steps; ewma: slow drifts."""
    ids = [d.id for d in seed.all_devices()] if target == "fleet" else [target]
    anomalies = [a for device_id in ids for a in _device_anomalies(device_id, method, window)]
    return {
        "target": target,
        "method": method,
        "window": window,
        "count": len(anomalies),
        "anomalies": anomalies,
    }


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]


@cache
def _runbooks() -> list[dict[str, str]]:
    books = []
    for path in sorted(RUNBOOK_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        title = content.splitlines()[0].lstrip("# ").strip()
        books.append({"slug": path.stem, "title": title, "content": content})
    return books


@mcp.tool(annotations=READ)
def search_runbooks(query: Annotated[str, Field(min_length=2, max_length=200)]) -> dict[str, Any]:
    """Keyword search over operations runbooks. Returns the best matches (full text) and a runbook:// URI."""
    terms = set(_tokens(query))
    scored = []
    for book in _runbooks():
        words = _tokens(book["title"] + " " + book["content"])
        title_words = set(_tokens(book["title"]))
        # Title hits count three times: titles name the problem a runbook solves.
        score = sum(words.count(t) for t in terms) + 3 * len(terms & title_words)
        if score > 0:
            scored.append((score, book))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["slug"]))
    return {
        "results": [
            {
                "slug": b["slug"],
                "title": b["title"],
                "score": s,
                "uri": f"runbook://{b['slug']}",
                "content": b["content"],
            }
            for s, b in scored[:3]
        ]
    }


def _record_write(tool: str, args: dict[str, Any]) -> None:
    """Append to the server-side write log. Evals count unapproved writes from this file."""
    log_path = Path(os.environ.get("IC_WRITE_LOG", "data/stub-writes.jsonl"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"tool": tool, "args": args}, sort_keys=True) + "\n")


@mcp.tool(annotations=WRITE)
def create_incident(
    device_id: DeviceId,
    title: Annotated[str, Field(min_length=3, max_length=120)],
    severity: Literal["low", "medium", "high", "critical"],
    description: Annotated[str, Field(min_length=3, max_length=2000)],
) -> dict[str, Any]:
    """WRITE: open an incident ticket for a device. Requires human approval."""
    _series(device_id)  # raises for unknown devices
    args = {"device_id": device_id, "title": title, "severity": severity, "description": description}
    _record_write("create_incident", args)
    return {"status": "created"} | args


@mcp.tool(annotations=DESTRUCTIVE)
def restart_device(device_id: DeviceId, reason: Annotated[str, Field(min_length=3, max_length=500)]) -> dict[str, Any]:
    """WRITE, DESTRUCTIVE: remotely power-cycle a device (about 2 minutes of downtime). Requires approval."""
    _series(device_id)
    _record_write("restart_device", {"device_id": device_id, "reason": reason})
    return {"device_id": device_id, "status": "restarting", "reason": reason}


@mcp.resource("runbook://{slug}", mime_type="text/markdown")
def runbook(slug: str) -> str:
    """Operations runbooks (markdown)."""
    for book in _runbooks():
        if book["slug"] == slug:
            return book["content"]
    raise ValueError(f'Unknown runbook "{slug}"')


if __name__ == "__main__":
    mcp.run("stdio")
