# incident-commander: specification

## Goal

Take a synthetic IoT fleet incident from alert to draft postmortem with a LangGraph
multi-agent graph: triage, parallel investigators, a synthesizer, a durable human
approval step for every write action, an executor that runs only approved actions,
and a postmortem writer. Reuse the MCP server from telemetry-ops-copilot as the tool
layer so the same problem exists once as a hand-written loop and once as a graph.

## Non-goals

- Real device data or real device control. All data is synthetic; writes are simulated.
- A general-purpose agent framework. One graph, one domain.
- Vector search, memory stores, extra agents (none are justified by a committed eval).
- Multi-tenant auth. One shared approver bearer token is enough for a local tool.
- Exactly-once writes (see ADR 0004: at-least-once with dedupe, window documented).

## Architecture

```
alert -> triage -> Send x N -> investigate (same subgraph, per-investigator READ tools)
      -> gather -> synthesizer -> prepare_approval -> approval (interrupt)
      -> executor (only node with WRITE tools) -> postmortem -> END
```

Budget gates sit at node boundaries: after triage, after gather and after synthesizer.
Any exceeded limit routes straight to postmortem with the matching stop_reason.

### Nodes (src/incident_commander/graph.py wires all of them)

| node | reads | writes | notes |
| --- | --- | --- | --- |
| triage | alert | triage, usage | category + severity + investigator list (metrics always) |
| investigate | InvestigatorInput (via Send) | findings, usage | compiled subgraph, max 3 tool calls, READ tools only |
| gather | findings, usage, limits | stop_reason (only if over budget) | fan-in point and budget gate |
| synthesizer | alert, triage, findings | diagnosis, proposed_actions, usage | actions validated against WRITE schemas |
| prepare_approval | proposed_actions | approval_request (approval_id, expires_at, hashes) | non-deterministic values live here, before the interrupt |
| approval | approval_request | decisions, stop_reason (rejected/expired) | interrupt(); resume with Command(resume=...) |
| executor | decisions, proposed_actions | executions | the only holder of WRITE tools; ledger dedupe |
| postmortem | everything | stop_reason, postmortem_path | writes runs/<uuid>/postmortem.md (DRAFT) |

### State schema (src/incident_commander/state.py)

- `alert: Alert` input, `mode: "mock" | "live"`, `started_at: str` (set by the caller, not a node)
- `limits: Limits` (max_steps, max_tokens, max_cost_usd), stored so they survive restarts
- `triage: Triage | None`
- `findings: dict[str, Finding]` reducer `merge_findings` (keyed by investigator, sorted)
- `usage: Usage` reducer `sum_usage` (steps, input_tokens, output_tokens, cost_usd)
- `diagnosis: Diagnosis | None`, `proposed_actions: list[ProposedAction]`
- `approval_request: ApprovalRequest | None` (approval_id, expires_at, actions with args_hash)
- `decisions: dict[str, Decision]` (action_id -> approve/edit/reject/expired + final args)
- `executions: list[Execution]`
- `stop_reason: StopReason | None` exactly one per finished run
- `postmortem_path: str | None`

## Tech choices

| choice | reason |
| --- | --- |
| Python 3.12+, uv | lock file, fast installs, one tool |
| langgraph 1.2.x StateGraph | explicit graph; Send, interrupt, Command, checkpointers are first-class |
| langchain-mcp-adapters | turns MCP tools into LangChain tools without glue code |
| official `mcp` SDK (FastMCP) | fleet-context server and the telemetry stub |
| AsyncSqliteSaver / AsyncPostgresSaver | MCP tools are async, so the graph is async end to end |
| FastAPI + StreamingResponse | SSE without an extra dependency |
| typer | readable subcommands with type hints |
| OpenTelemetry SDK + custom JSONL exporter | standard spans, zero infrastructure |
| ruff, mypy (strict on src), pytest | the quality gate before every push |

## Threat model

Assets: fleet devices (restart is destructive), incident tracker, approver token, API key.

| threat | mitigation | proof |
| --- | --- | --- |
| model proposes or calls a write without approval | WRITE tools bound only to the executor; executor runs only actions with an approve/edit decision whose hash matches | tests/test_structural_safety.py, compromised-model tests and evals |
| prompt injection in runbooks, events, config notes | tool output is data inside the prompt; it cannot reach decisions, which come only from the resume payload | injection fixtures, eval cases `inj-*` |
| stale or replayed approval | decisions bound to (thread_id, approval_id, action_id, args_hash); expiry checked | tests/test_approval.py, API 409 tests |
| approver widens scope via edit | edited args re-validated with the same schema; tool and action set fixed | tests/test_approval.py |
| double execution after crash | ledger keyed by (thread_id, action_id), checked before each write | tests/test_ledger.py, crash tests |
| path traversal via thread_id | thread_id must parse as a UUID before any filesystem access | tests/test_paths.py |
| token or key leaks | secrets never put in state; trace attributes redacted; API refuses non-local bind without token | tests/test_tracing.py, tests/test_api.py |
| unauthenticated approval | bearer token on every decision endpoint | tests/test_api.py |

## Milestones (each verified by a command)

| id | scope | success criterion |
| --- | --- | --- |
| M0 | spec, skeleton, CI | `ruff check . && mypy && pytest` exit 0 |
| M1 | MCP bridge | `pytest tests/test_mcp_contract.py` |
| M2 | models, budgets | `pytest tests/test_models.py tests/test_budgets.py`; two fake runs byte-identical |
| M3 | graph without writes | `ic run --alert tests/fixtures/alerts/thermal_d17.json` writes a postmortem; `pytest tests/test_graph.py` |
| M4 | approval, durability | `pytest tests/test_crash.py tests/test_structural_safety.py` |
| M5 | time travel, tracing | `pytest tests/test_timetravel.py tests/test_tracing.py` |
| M6 | API, approval page | `pytest tests/test_api.py` incl. crash test c |
| M7 | evals | `python -m evals.run --mode mock` exit 0, no diff on rerun |
| M8 | comparison, packaging, docs | `python scripts/check_dod.py`, `docker compose config` |

## Risks

- LangGraph APIs move: every API used was confirmed against the installed 1.2.20 source.
- Subprocess crash tests can be flaky: they poll checkpoint state instead of sleeping.
- The fake model only proves wiring; the README says so next to every number.

## Deviations

(Each entry is also in DECISIONS.md.)

- D1. `gh` CLI token invalid in the build environment; the repository was created by the owner
  and attached through the GitHub connector. Pushes use plain git.
- D2. `mcp==2.x` conflicts with `langchain-mcp-adapters 0.3.2` (needs `mcp<2`); pinned `mcp` 1.x.
