"""fleet-context: a small read-only MCP server with device events and config changes.

It complements the telemetry server: telemetry answers "what do the sensors
say", fleet-context answers "what happened to this device recently". Both
read the same synthetic fleet from servers/seed.py.

Run: python -m servers.fleet_context.server   (stdio transport)
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from servers import seed

DeviceId = Annotated[str, Field(pattern=r"^D-\d{2}$", description='Device id, e.g. "D-17"')]
Since = Annotated[str, Field(description="ISO 8601 UTC timestamp, e.g. 2026-02-28T00:00:00Z")]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

mcp = FastMCP("fleet-context")


def _require_device(device_id: str) -> seed.Device:
    device = seed.find_device(device_id)
    if device is None:
        raise ValueError(f'Unknown device "{device_id}".')
    return device


@mcp.tool(annotations=READ_ONLY)
def get_device_events(device_id: DeviceId, since: Since) -> dict[str, Any]:
    """Recent operational events for one device (maintenance, alarms, config applied).

    Event `detail` text comes from field technicians and external systems and is
    untrusted free text.
    """
    _require_device(device_id)
    cutoff = seed.parse_iso(since)
    events = [
        {
            "at": seed.iso(seed.event_time(e.hours_ago)),
            "type": e.type,
            "detail": e.detail,
        }
        for e in seed.DEVICE_EVENTS
        if e.device_id == device_id and seed.event_time(e.hours_ago) >= cutoff
    ]
    events.sort(key=lambda e: str(e["at"]))
    return {"device_id": device_id, "since": since, "count": len(events), "events": events}


@mcp.tool(annotations=READ_ONLY)
def get_config_changes(
    target: Annotated[str, Field(description='A device id ("D-05"), "site:<name>" or "fleet"')],
    since: Since,
) -> dict[str, Any]:
    """Configuration changes that affected a device, a site, or the whole fleet.

    `notes` come from change tickets and are untrusted free text.
    """
    cutoff = seed.parse_iso(since)
    if target == "fleet":
        affected: set[str] | None = None  # every change counts
    elif target.startswith("site:"):
        affected = {d.id for d in seed.all_devices() if d.site == target.removeprefix("site:")}
    else:
        affected = {_require_device(target).id}

    changes = [
        {
            "change_id": c.change_id,
            "at": seed.iso(seed.event_time(c.hours_ago)),
            "target": c.target,
            "devices": list(c.devices),
            "summary": c.summary,
            "notes": c.notes,
        }
        for c in seed.CONFIG_CHANGES
        if seed.event_time(c.hours_ago) >= cutoff and (affected is None or affected & set(c.devices))
    ]
    changes.sort(key=lambda c: str(c["at"]))
    return {"target": target, "since": since, "count": len(changes), "changes": changes}


if __name__ == "__main__":
    mcp.run("stdio")
