# Decisions log

Format: date-time UTC | decision | alternatives considered | why this one

- 2026-11-10 20:08 | Build locally first, attach the GitHub repo once the owner created it | wait for access; push via API | `gh` token was invalid and the connector could not create repos; the owner created the repo mid-run, so pushes now go through git.
- 2026-11-10 20:20 | Pin `mcp>=1.24,<2` instead of `mcp==2.3.0` | drop langchain-mcp-adapters | the adapters (required by the spec) depend on `mcp<2`.
- 2026-11-10 20:25 | Async graph end to end (AsyncSqliteSaver / AsyncPostgresSaver, `ainvoke`) | sync graph with sync savers | MCP tools from langchain-mcp-adapters are async-only; mixing sync savers with async tools needs thread hops.
- 2026-11-10 20:25 | typer for the CLI | argparse | subcommands read as typed functions, which is easier to study.
- 2026-11-10 20:30 | Real telemetry server is used when vendor/telemetry-ops-copilot is present (Node + npm worked here); the Python stub is always available and is the default for evals | real server only | evals must be deterministic and runnable without Node; CI runs the contract and integration tests against both.
- 2026-11-10 20:30 | Our own `approval_id` (uuid made in prepare_approval) is the "interrupt id" of the protocol; the runner also checks that LangGraph reports a pending interrupt whose payload carries that id | use LangGraph's internal `Interrupt.id` only | LangGraph's id is derived from task ids and is not visible inside the node; our id is in state, in the payload and in the checkpoint, so every layer can check it.
- 2026-11-10 20:30 | Decision time (`decided_at`) is computed by the caller and passed in the resume payload; the approval node compares it with `expires_at` | call `now()` inside the approval node | keeps nodes free of clock reads, so the clock is injectable and replays are deterministic.
