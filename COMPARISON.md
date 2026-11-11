# Hand-written loop vs LangGraph

The same job built twice. [telemetry-ops-copilot](https://github.com/HashirAKB/telemetry-ops-copilot)
(pinned at `2cf208c`) is a hand-written TypeScript agent loop over an MCP server. This repo drives the same
MCP server with an explicit LangGraph `StateGraph`. Both have a deterministic mock model, approval for every
write, budgets, tracing, an HTTP API and evals. All numbers below come from scripts in this repo and can be
re-run.

## Lines of code by component

Counted by `python -m scripts.loc_compare`: non-blank, non-comment lines (Python docstrings and `#`
lines, TypeScript `//` and `/* */` blocks are comments; tests excluded). Components are mapped by
responsibility, so each row compares code that does the same job.

<!-- loc:start -->
| component | hand-written loop (TS) | LangGraph (Python) |
| --- | --- | --- |
| Orchestration (loop or graph, state, routing, prompts) | 174 | 573 |
| Human approval and write safety | 120 | 274 |
| Persistence, resume, time travel | 104 | 344 |
| Budgets and pricing | 10 | 53 |
| Model providers (live + mock) | 412 | 473 |
| MCP client and tool registry | 89 | 116 |
| HTTP API and live streaming | 169 | 214 |
| Tracing | 144 | 110 |
| CLI and config | 68 | 306 |
| Approval UI | 268 | 201 |
| Evals | 251 | 403 |
| **total (agent side)** | **1809** | **3067** |
| MCP servers and data (not in total) | 614 | 433 |
<!-- loc:end -->

The LangGraph version is about 1.7x larger on the agent side. The framework did not shrink the code. It
paid for durable pause and resume, parallel branches and time travel, which the loop does not have. Most
of the extra lines are orchestration (seven nodes plus a subgraph, each small and tested on its own),
approval binding (hashes, expiry, the ledger) and CLI commands for history, replay, fork and sweep.

## Guarantees

| guarantee | hand-written loop (TS) | LangGraph (this repo) | evidence here |
| --- | --- | --- | --- |
| Durability of a run | finished runs stored in SQLite; a run in flight is marked `interrupted` on restart | state checkpointed after every step (SQLite or Postgres) | `tests/test_crash.py` |
| Resume after crash | no: the run is lost | yes, from the last checkpoint in a new process; finished branches are not re-run | crash test b (counts tool spans) |
| Pending approval survives restart | no ("cannot survive a server restart", its README) | yes: a paused run is approved hours later from any process | crash tests a and c |
| Approval expiry | in-memory 5 minute timer in the HTTP approver; lost on restart | `expires_at` stored in state before the interrupt; sweeper + injectable clock | `tests/test_approval_flow.py`, `tests/test_api.py` |
| Approval binding | per call, in process | (thread, approval id, action id, args hash); 409 on stale, tampered or expired | `tests/test_approval.py` |
| Duplicate writes after crash | not applicable (no resume) | ledger dedupe, at-least-once with one small window (ADR 0004) | `tests/test_approval_flow.py::test_replaying_the_executor_never_writes_twice` |
| Parallelism | sequential tool calls | investigators fan out with `Send`, merged by keyed reducers | `tests/test_graph.py` |
| Replay / time travel | replay of recorded LLM responses (a test provider) | `ic history`, `ic replay`, `ic fork` from any checkpoint, zero repeated writes | `tests/test_timetravel.py` |
| Streaming | SSE of trace spans for runs in flight | SSE of graph updates + custom progress; late subscribers get a snapshot first | `tests/test_api.py::test_sse_sends_snapshot_then_node_events_in_order` |
| Budgets | tokens and cost per run, step limit | steps, tokens and cost per run, split per parallel branch, one stop_reason per run | `tests/test_graph.py` |
| Who holds write tools | the model sees them; the loop asks a human before running one | only the executor node; no model is ever bound to them | `tests/test_structural_safety.py` |

## Mock-mode latency per run

Measured by `python -m scripts.latency_compare` (results committed in `docs/latency.json`). Both sides use
their mock model, so this is framework and process overhead, not model latency: a real model adds
seconds per call and dominates both.

<!-- latency:start -->
Median wall time in ms, mode=mock, Linux x86_64, Python 3.13.18 (5 cold runs, 10 warm runs).

| scenario | hand-written loop (TS) | LangGraph (Python) |
| --- | --- | --- |
| cold CLI, healthy device, no writes | 576 | 2176 |
| warm, healthy device, no writes | N/A | 67 |
| warm, pause + approve + 2 writes | N/A | 91 |
<!-- latency:end -->

The TS loop starts about 4x faster from cold: its MCP server runs in the same process, while this graph
imports LangChain and LangGraph and starts two stdio MCP servers. Warm, a full graph run with three
parallel investigators takes well under 100 ms. The TS repo has no in-process entry point the script
could time, so its warm cells are N/A.

## What was harder with LangGraph

- **Re-execution on resume.** A node with `interrupt()` re-runs from its first line. Getting deadlines and
  ids right needed a separate `prepare_approval` node (ADR 0003). The bug this avoids is silent.
- **Typing.** LangGraph's generics and `add_node` overloads fight strict mypy (node protocols must name
  their parameter `state`); a few `type: ignore` comments remain at library boundaries.
- **Version drift.** APIs moved between releases (`durability=`, `input_schema`/`output_schema`, interrupt
  objects). Every call was checked against the installed 1.2.20 source before use.
- **Subgraph outputs.** Parallel branches must not write the same plain key; the investigator needs an
  explicit output schema, or the run fails with a concurrent-update error.
- **Checkpoint plumbing details.** `aupdate_state` on SQLite needs `checkpoint_ns` in the config; the
  MCP adapter adds random ids to content blocks, which broke deterministic output until tool results were
  flattened to text.

## What was easier with LangGraph

- **Durable human-in-the-loop.** `interrupt()` + `Command(resume=...)` + a checkpointer is the whole
  mechanism. In the loop this is the feature the author listed as "what I'd do next".
- **Crash recovery.** Resuming a dead run is `ainvoke(None, config)`; completed parallel branches are kept
  as pending writes and are not re-run.
- **Fan-out and fan-in.** `Send` plus a reducer replaces hand-written `Promise.all` bookkeeping, with
  deterministic merge order.
- **Time travel for free.** History, replay and fork are thin wrappers over checkpoints the graph writes
  anyway.
- **Streaming.** `stream_mode=["updates", "custom"]` gives node events and progress events with no extra
  event bus.

## Verdict

Use the 200-line loop when a run is short, synchronous and someone is watching it: a CLI assistant, a
chat turn, a single tool-using answer. It starts faster, has fewer moving parts and is easier to read top
to bottom. A pause there lives in memory, which is fine when the human answers within seconds.

Use LangGraph (or another durable-execution runtime) when a run must outlive the process: approvals that
take hours, workers that restart, runs that you need to inspect or replay after the fact, or several
branches whose results must merge safely. For this incident flow those properties are the point, so the
extra code is justified. If they were not needed, the loop would be the better choice.
