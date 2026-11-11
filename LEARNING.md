# Study guide

How to learn LangGraph from this repo, and how to defend its design in an interview. Read it next to
the code; every claim points at a file and a function.

## Goals

1. **The LangGraph mental model**: state, nodes, edges, reducers, conditional routing, subgraphs.
2. **Fan-out and fan-in** with `Send`, and race-free merging.
3. **Durable execution**: checkpointers, `thread_id`, resume after a crash, and why this is hard in a
   hand-written loop.
4. **Human-in-the-loop**: `interrupt()`, resuming with edits, expiry.
5. **Debugging**: stream modes, replay from checkpoints, tracing.
6. **Bridging MCP into LangGraph**.
7. **Judging frameworks**: what the framework bought and what it cost (COMPARISON.md).

## 1. The mental model in five sentences

- **State** is one TypedDict (`state.py::IncidentState`). Every node gets the whole state and returns a
  partial update (a dict of only the keys it changed).
- **Nodes** are plain async functions (`nodes/*.py`). They never call each other.
- **Edges** decide what runs next: fixed (`graph.add_edge`) or computed by a routing function
  (`graph.add_conditional_edges`, see `routing.py`). Routing functions read state and return a node name
  or a list of `Send` objects; they cannot write state.
- **Reducers** say how an update merges into a key. Without one, the last write wins and two parallel
  writes to the same key are an error. With one (`Annotated[..., reducer]`), updates are combined:
  `merge_findings` merges by investigator name, `sum_usage` adds numbers.
- **A checkpointer** saves the state after every step, keyed by `thread_id`. That one fact gives you
  pause/resume, crash recovery, history and time travel.

## 2. Reading order

| # | file | what it teaches |
| --- | --- | --- |
| 1 | `src/incident_commander/state.py` | the state schema, plain-JSON values, the two reducers |
| 2 | `src/incident_commander/graph.py` | the whole graph in one function; where WRITE tools enter |
| 3 | `src/incident_commander/routing.py` | conditional edges, `Send` fan-out, the per-branch budget split |
| 4 | `src/incident_commander/nodes/triage.py` | the simplest node: one structured model call |
| 5 | `src/incident_commander/nodes/investigator.py` | a subgraph with input/output schemas, an explicit model-and-tools loop, refusing unbound tools |
| 6 | `src/incident_commander/nodes/budget.py` | a fan-in node and why `budget_stop` exists |
| 7 | `src/incident_commander/nodes/synthesizer.py` | the model proposes, code validates (`validate_proposals`) |
| 8 | `src/incident_commander/nodes/approval.py` | `interrupt()`, re-execution on resume, why deadlines live in `prepare_approval` |
| 9 | `src/incident_commander/approvals.py` | binding a human decision to an exact request |
| 10 | `src/incident_commander/nodes/executor.py` + `ledger.py` | the only writer; dedupe across crashes |
| 11 | `src/incident_commander/nodes/postmortem.py` | exactly one `stop_reason` per run; a pure render function |
| 12 | `src/incident_commander/runner.py` | `astream` with `durability="sync"`, `Command(resume=...)`, status from a snapshot |
| 13 | `src/incident_commander/checkpointer.py` | SQLite vs Postgres behind one function |
| 14 | `src/incident_commander/timetravel.py` | `aget_state_history`, replay, fork with `aupdate_state` |
| 15 | `src/incident_commander/mcp_bridge.py` + `registry.py` | MCP tools as LangChain tools; classification from a contract |
| 16 | `src/incident_commander/models/fake.py` | what a chat model must implement for LangGraph (`bind_tools`, structured output, usage) |
| 17 | `src/incident_commander/api.py` | background runs, SSE, auth, the expiry sweeper |
| 18 | `tests/test_structural_safety.py`, `tests/test_crash.py` | how the two headline claims are proven |
| 19 | `evals/baseline.py` | the prebuilt agent, for contrast |

## 3. One incident, end to end

Alert `tests/fixtures/alerts/signal_d33.json`: D-33 signal below -100 dBm. Command:
`ic run --alert tests/fixtures/alerts/signal_d33.json`. The checkpoints below are what `ic history`
printed for this run ([docs/screenshots/cli.txt](docs/screenshots/cli.txt)).

| step | what ran | what the checkpoint holds afterwards | next |
| --- | --- | --- | --- |
| -1 | `runner.start_run` passes the input | nothing yet (input pending) | `__start__` |
| 0 | input written to state | `alert`, `mode=mock`, `started_at`, `limits`, `approval_ttl_seconds`, empty `findings`, zero `usage` | `triage` |
| 1 | `triage` (one structured model call) | `triage = {category: connectivity, severity: high, investigators: [metrics, events, runbooks]}`, usage +1 step | `investigate` x3 |
| 2 | `routing.fan_out_investigators` returned three `Send`s; the three branches ran in parallel. Each branch's subgraph also checkpoints under its own namespace | `findings = {events, metrics, runbooks}` merged and sorted by `merge_findings`; `usage` is the sum of all branches | `gather` |
| 3 | `gather` (fan-in; budget check in `route_after_gather`) | unchanged | `synthesizer` |
| 4 | `synthesizer`: diagnosis `connectivity`, two actions proposed and validated | `diagnosis`, `proposed_actions = [A1 restart_device D-33, A2 create_incident D-33]`, `dropped_proposals = []` | `prepare_approval` |
| 5 | `prepare_approval`: new `approval_id`, `requested_at`, `expires_at = now + 4h`, an args hash per action | `approval_request` | `approval` |
| 5 (paused) | `approval` calls `interrupt(request)`; the run stops; `ic run` prints the request and exits | same as step 5 plus a pending interrupt carrying the request | waiting |
| 6 | hours later, any process: `ic approve` -> `runner.decision_command` validates against the pending request -> `drive(Command(resume=payload))`; `approval` re-runs from its first line, `interrupt()` now returns the payload | `decisions = {A1: approve, A2: reject}` | `executor` |
| 7 | `executor`: A1 checked in the ledger, executed through the MCP server, recorded in the ledger | `executions = [A1 executed]` | `postmortem` |
| 8 | `postmortem`: `final_stop_reason` = completed; writes `runs/<id>/postmortem.md` | `stop_reason = completed`, `postmortem_path` | END |

If the process died between steps 1 and 2, `ic resume` would call `drive(None)`: LangGraph loads the last
checkpoint, sees which `investigate` tasks already saved their writes, and re-runs only the missing ones
(crash test b counts the tool spans to prove it).

## 4. Fan-out, fan-in and race-free merging

- `routing.fan_out_investigators` returns `[Send("investigate", InvestigatorInput(...)), ...]`. Each `Send`
  runs the same node with its own input, in the same step, concurrently.
- The investigator subgraph declares `output_schema=InvestigatorOutput` (only `findings` and `usage`), so
  a branch cannot write `alert` or any other plain key back to the parent; that would be a concurrent
  update error.
- `merge_findings` is keyed by investigator name and sorted. Running a branch twice (resume, replay)
  replaces its finding; finishing order never changes the result. `sum_usage` adds spend from all branches.
- `gather` is where LangGraph waits for every branch (all `investigate` tasks finish before the next step).

## 5. Durable execution

- `checkpointer.get_checkpointer` returns `AsyncSqliteSaver` or `AsyncPostgresSaver`. The graph is compiled
  with it (`graph.build_graph`), and every call passes `{"configurable": {"thread_id": ...}}`
  (`runner.config_for`).
- `runner.drive` streams with `durability="sync"`: each checkpoint is on disk before the next step starts,
  so a SIGKILL loses at most the step in flight.
- Why this is hard in a hand-written loop: the loop's state is local variables and a call stack. To survive
  a restart you must serialize the conversation, the position in the loop, pending tool calls, the pending
  approval and its deadline, and write code that rebuilds the loop from that. telemetry-ops-copilot stores
  finished runs but marks in-flight runs `interrupted` for exactly this reason.
- Side effects are the hard part even with a checkpointer: a node that crashes after a write re-runs on
  resume. `ledger.py` handles that (ADR 0004).

## 6. Human-in-the-loop

- `nodes/approval.py::make_approval`: `payload = interrupt(request)`. The first time, this raises an
  internal signal and the run pauses; on resume, the node runs again from the top and `interrupt()` returns
  the value given in `Command(resume=...)`.
- Therefore nothing before `interrupt()` may have side effects or read the clock. `prepare_approval`
  computes the id and deadline once and checkpoints them (ADR 0003).
- Edits: `approvals.normalize_decisions` re-validates edited args with the tool's schema
  (`actions.validate_write_args`), refuses a changed tool, unknown fields and new action ids, and treats a
  missing decision as reject.
- Expiry: `runner.expiry_command` builds an `expired` resume once the injected clock passes `expires_at`;
  `ic sweep-expired` and the API's background sweeper call it.

## 7. Debugging

- **Stream modes**: `runner.drive` uses `stream_mode=["updates", "custom"]` with `subgraphs=True`.
  `updates` gives each node's returned dict; `custom` carries progress written with `get_stream_writer()`
  inside `nodes/investigator.py::run_tools`.
- **History and time travel**: `ic history <id>`, `ic replay <id> --checkpoint <cid>`, `ic fork <id>
  --checkpoint <cid> --set findings.metrics.summary=...` (`timetravel.py`).
- **Tracing**: `tracing.py` writes one OpenTelemetry span per node, model call and tool call to
  `traces/<id>.jsonl`; `ic trace <id>` prints the timeline.

## 8. MCP into LangGraph

- `mcp_bridge.open_toolbox` opens one long-lived session per server (`MultiServerMCPClient.session`) and
  loads the tools with `load_mcp_tools`. The simpler `client.get_tools()` would start a new stdio process
  per tool call.
- `registry.split_tools` classifies each tool from `docs/mcp-contract.md`; a server cannot promote a tool by
  lying in its `readOnlyHint`.
- `mcp_bridge.tool_output_text` flattens MCP content blocks to text, because the adapter adds random ids.

## 9. Fifteen interview questions

1. **Why an explicit StateGraph instead of `create_react_agent`?** Fixed stages with different tools and
   safety rules per stage; every route is a named, tested function (`routing.py`). The prebuilt agent is
   kept in `evals/baseline.py`; the eval shows it uses 2.8x the tokens because it re-sends its history every
   turn, and the model holds the write tools (ADR 0001).
2. **How do you guarantee no write without approval?** Structure, not prompts: WRITE tools are passed only
   to `make_executor` in `graph.py`, and the executor runs only actions with an approve/edit decision.
   `tests/test_structural_safety.py` walks every node's reachable objects, spies on every `bind_tools`
   call, and greps `src/` (ADR 0002).
3. **What happens to code before `interrupt()` on resume?** It runs again. That is why
   `nodes/approval.py` has two nodes: `prepare_approval` makes the id and deadline once, `approval` only
   reads state before calling `interrupt()` (ADR 0003).
4. **How is a decision bound to what the approver saw?** `approvals.normalize_decisions` checks
   `approval_id` (stale requests fail), action ids (no new actions), the sha256 args hash from
   `actions.args_hash` (the args did not change), the tool, and `decided_at` against `expires_at`. The
   runner refuses before resuming (HTTP 409); the node re-checks in case someone resumed directly.
5. **What if the process dies right after restarting a device?** If the ledger row was written: resume
   re-runs the executor, `Ledger.get` finds it, the action is `skipped_duplicate`. If it died between the
   MCP call and the ledger insert, the write repeats: at-least-once with dedupe, documented in
   `ledger.py` and ADR 0004.
6. **How do parallel branches merge without races?** A keyed reducer (`state.merge_findings`) plus an
   output schema on the subgraph, so branches only write reducer-backed keys. Sorting makes the result
   independent of completion order; `tests/test_graph.py` replays the fan-out and checks for no duplicates.
7. **How do budgets work across parallel branches?** `fan_out_investigators` splits the remaining budget
   evenly (`budgets.split`); each branch stops at its share; the totals are summed and checked at `gather`.
   The tradeoff is that unused share is not lent between branches (ADR 0005).
8. **Why does every run have exactly one stop_reason?** Paths are mutually exclusive: `budget_stop` writes
   a limit reason, `approval` writes rejected/expired, `postmortem.final_stop_reason` derives
   completed/no_action_needed only if nothing was set, and `runner.record_error` writes `error` with
   `as_node="postmortem"` so the run cannot continue.
9. **How does a paused run survive a restart?** The interrupt and the request are in the checkpoint. Any
   process with the same checkpointer calls `runner.get_status` (it sees `snapshot.interrupts`) and resumes
   with `Command(resume=...)`. Crash tests a and c kill the paused process and approve from a new one.
10. **How do you resume a run that crashed mid-step?** `runner.resume_run` calls `drive(None)`; LangGraph
    continues from the last checkpoint and reuses pending writes of tasks that finished
    (`tests/test_crash.py::test_b_...`).
11. **How do replay and fork avoid repeating writes or faking approvals?** Both stay on the same thread, so
    the ledger still applies; new actions go through `prepare_approval` and `approval` again; forks may only
    change triage, findings or diagnosis (`timetravel.FORKABLE_FIELDS`).
12. **How are prompt injections handled?** As data: they reach models inside JSON blocks; a hijacked model
    (the fake's compromised mode) can only propose actions, which are validated, capped
    (`actions.MAX_ACTIONS`) and shown to a human. Eval cases `i01` to `i04` and `p23` to `p25` exercise it.
13. **Why a fake model, and what do mock evals prove?** Determinism and zero cost: `FakeChatModel` gives
    byte-identical runs, so CI can diff eval results. Its rules were written for this fleet, so mock scores
    prove wiring and safety gates, not diagnostic quality; the README says so next to every number.
14. **How does the API stream progress to late subscribers?** `api.EventLog` keeps a per-thread buffer of
    the current drive's events; `/runs/{id}/events` sends a checkpoint snapshot first, then the buffer,
    then live events, then `end`.
15. **When would you not use LangGraph?** For short, synchronous runs with a human watching: the TS loop
    is about 1.7x smaller and starts 4x faster cold (COMPARISON.md). LangGraph earns its cost when runs
    must outlive processes, be inspected or replayed, or merge parallel work.

## 10. Five exercises

1. **Break the deadline.** Move the `expires_at` computation from `prepare_approval` into `approval`, before
   `interrupt()`. Which test fails, and why? *Hint*: compute it with the injected clock and run
   `tests/test_approval_flow.py::test_expiry_via_injected_clock`; think about what the clock reads on resume.
2. **Remove the reducer.** Change `findings` in `state.py` to a plain `dict` without `merge_findings`. Run
   `tests/test_graph.py`. *Hint*: read the error LangGraph raises when three branches write one key in the
   same step.
3. **Add an investigator.** Add `firmware` with a new READ tool `get_firmware_history(device_id)` in
   `servers/fleet_context/server.py`. *Hint*: the contract table in `docs/mcp-contract.md`, the
   `InvestigatorName` literal, `fake_rules.investigator_plan`, and the contract test all need it.
4. **Hand a write tool to an investigator** by adding `restart_device` to its tool list in
   `nodes/investigator.py`. Which safety test catches it? *Hint*: `reachable_tools` in
   `tests/test_structural_safety.py`.
5. **Close the at-least-once window.** Make `restart_device` in the stub accept an `idempotency_key` and
   ignore repeats, and pass `f"{thread_id}:{action_id}:{args_hash}"` from the executor. *Hint*: write a test
   that calls the tool twice with the same key and counts lines in the write log.
