# Summary

## What was built

A LangGraph multi-agent incident responder for a synthetic IoT fleet. An alert goes through triage, a
parallel fan-out of four investigators (one compiled subgraph with READ-only MCP tools each), a
synthesizer, a durable human approval step with expiry, an executor that is the only holder of write tools
and dedupes through a ledger, and a postmortem writer. It runs from a CLI (`ic`) and an HTTP API with SSE
and a static approval page, checkpoints to SQLite or Postgres, supports history, replay and fork, writes
OpenTelemetry spans to JSONL, and is evaluated against a `create_react_agent` baseline on 39 cases. Tools
come from the real telemetry-ops-copilot MCP server (pinned commit, stdio or HTTP) or a Python stub with
the same contract, plus a new fleet-context MCP server.

## Verification status per milestone

| milestone | verified by | where it ran |
| --- | --- | --- |
| M0 skeleton | ruff, mypy (strict), pytest | locally and CI |
| M1 MCP bridge | `tests/test_mcp_contract.py`: stub, real server over stdio, real server over HTTP | locally and CI (real server via Node in CI) |
| M2 models and budgets | `tests/test_models.py`, `tests/test_budgets.py` (byte-identical fake runs) | locally and CI |
| M3 graph | `tests/test_graph.py`, `ic run` writes a postmortem | locally and CI |
| M4 approval and durability | `tests/test_structural_safety.py`, `tests/test_approval*.py`, `tests/test_ledger.py`, crash tests a and b | SQLite locally and CI; Postgres locally (Postgres 16) and in the CI Postgres job |
| M5 time travel and tracing | `tests/test_timetravel.py`, `tests/test_tracing.py` | locally and CI |
| M6 API and approval page | `tests/test_api.py`, crash test c | locally and CI |
| M7 evals | `python -m evals.run --mode mock` exits 0 with 0 unapproved writes for both systems; regeneration is byte-identical | locally and CI (determinism check) |
| M8 comparison, packaging, docs | `scripts/loc_compare.py`, `scripts/latency_compare.py`, `scripts/check_dod.py`, `docker compose config`, `docker build` of both images | scripts locally and in CI; image builds in CI only (green) |

Not run: live evals and the live smoke test (no ANTHROPIC_API_KEY in the build environment; the smoke test
skips itself), `docker build` and `docker compose up` locally (Docker Hub rate limit and the sandbox HTTPS
proxy); the approval page was exercised through its HTTP API in tests, not in a browser.

## Mocked vs real

- Real: LangGraph runtime, checkpointers (SQLite and Postgres), MCP over stdio and streamable HTTP, the
  telemetry-ops-copilot server at a pinned commit (contract tests and one full approve-and-write run,
  counted in its own SQLite tables), FastAPI, SSE, OpenTelemetry
  spans, SIGKILL crash recovery.
- Mocked: the model (a deterministic rule-based fake in every committed number), device writes
  (simulated by the MCP servers), all fleet data (synthetic), prices in mock mode (fake, labeled).

## Known gaps

- No live-model results; mock scores measure wiring and safety gates only.
- At-least-once writes: one small window between the device write and the ledger insert.
- One shared approver token; one API process per checkpointer.
- Evals use the telemetry stub, not the real server.
- Docker images are built in CI only, and `docker compose up` has not been run end to end anywhere.

## Reviewer pass findings (fixed)

- The executor now refuses an approved action whose tool or args changed after the human saw it
  (`tests/test_approval_flow.py::test_executor_refuses_an_action_changed_after_approval`).
- A new run can no longer reuse an existing thread id (it would have merged into the old run's state).
- Budget overrides in `POST /runs` are validated (positive numbers, known keys only).
- Mock eval results differed between Python 3.12 and 3.13 (docstring dedent in 3.13 changed MCP tool
  descriptions); descriptions are now normalized, results verified identical on both.
- Added an end-to-end run against the real telemetry server and a recorded-response run.

Git history: all project commits are authored and committed by the configured identity; the first commit
(an empty README) was created by the repository owner on github.com and has GitHub as committer.

## Resume bullets (each backed by code and tests in this repo)

- Built a LangGraph multi-agent incident responder (parallel `Send` fan-out, keyed reducers, typed state)
  whose human approvals survive process death: three SIGKILL crash tests resume runs from SQLite and
  Postgres checkpoints with exactly one execution per approved action.
- Made write safety structural: write tools are reachable only from one executor node, proven by a graph
  walk, a spy model and a source grep, with 0 unapproved writes across 39 eval cases including
  prompt-injection and hijacked-model cases (mock mode).
- Bridged two MCP servers (an existing TypeScript server pinned by commit, over stdio or HTTP, and a new
  Python FastMCP server) into LangGraph with a contract-driven READ/WRITE tool registry.
- Measured the framework honestly against my earlier hand-written loop: LOC by component, guarantees,
  mock-mode latency and a deterministic eval harness against a `create_react_agent` baseline, all
  regenerated by scripts in CI.
