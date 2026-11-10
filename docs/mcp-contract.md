# MCP tool contract

This file is the source of truth for which tools exist and whether they are READ or WRITE.
`src/incident_commander/registry.py` parses the table between the `contract` markers at
startup. A tool that a server offers but this table does not list is rejected: the run
refuses to start. Changing a tool's class therefore needs a reviewed change to this file.

Telemetry server: [telemetry-ops-copilot](https://github.com/HashirAKB/telemetry-ops-copilot)
pinned at commit `2cf208c5183a3077358c6d628fa68bcbf1d6205f` (see `scripts/fetch_telemetry.sh`),
or the Python stub in `servers/telemetry_stub` with the same names and arguments.

<!-- contract:start -->
| tool | server | access | arguments (required in bold) | investigator |
| --- | --- | --- | --- | --- |
| list_devices | telemetry | READ | site, battery_below | metrics |
| get_metrics | telemetry | READ | **device_id**, window | metrics |
| detect_anomalies | telemetry | READ | **target**, method, window | metrics |
| search_runbooks | telemetry | READ | **query** | runbooks |
| create_incident | telemetry | WRITE | **device_id**, **title**, **severity**, **description** | none |
| restart_device | telemetry | WRITE | **device_id**, **reason** | none |
| get_device_events | fleet-context | READ | **device_id**, **since** | events |
| get_config_changes | fleet-context | READ | **target**, **since** | config_changes |
<!-- contract:end -->

## Argument details

- `device_id`: `^D-\d{2}$` (50 synthetic devices, `D-01` to `D-50`).
- `window`: `1h | 6h | 24h | 7d`, default `24h`. `method`: `zscore | ewma`, default `zscore`.
- `target` (detect_anomalies): a device id or `fleet`. `target` (get_config_changes): a device id,
  `site:<name>` or `fleet`.
- `since`: ISO 8601 UTC timestamp. The fleet clock ends at `2026-03-01T00:00:00Z`.
- `severity`: `low | medium | high | critical`. `title` 3 to 120 chars, `description` 3 to 2000,
  `reason` 3 to 500.

## Write tools

| tool | effect | destructive | server-side record |
| --- | --- | --- | --- |
| create_incident | opens an incident ticket | no | real: `incidents` table; stub: `IC_WRITE_LOG` JSONL |
| restart_device | power-cycles a device (simulated) | yes | real: `device_events` table; stub: `IC_WRITE_LOG` JSONL |

Write tools are bound to exactly one graph node, the executor (`nodes/executor.py`). The MCP
`readOnlyHint` and `destructiveHint` annotations are informational only: classification comes
from this file, never from a server, because a server could lie about its own tools.

## Stub differences from the real server

| aspect | real server | stub |
| --- | --- | --- |
| metric resolution | 1 minute | 5 minutes, per-device random stream |
| runbook search | BM25 with synonyms | keyword overlap with a title bonus |
| write record | SQLite tables | JSONL write log |
| injected anomalies | D-17, D-08, D-33, D-42, D-05/D-21/D-46 | the same devices and shapes |

## Untrusted content

`search_runbooks` returns `vendor-bulletin.md`, which contains a deliberate prompt-injection
instruction. fleet-context returns two more: an operator note on `D-24` and the change note of
`CHG-103` on `D-29`. These fixtures prove that tool output cannot cause a write.
