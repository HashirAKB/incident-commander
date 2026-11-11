"""Lines of code by component: telemetry-ops-copilot (hand-written TS loop) vs this repo.

Method: count non-blank lines that are not comments. Python: `#` lines and
docstrings are comments. TypeScript: `//` lines and `/* ... */` blocks are
comments. HTML: non-blank lines. Tests are excluded on both sides.

Needs the TS repo at vendor/telemetry-ops-copilot (scripts/fetch_telemetry.sh).
Prints a markdown table; COMPARISON.md contains this exact output.

    python -m scripts.loc_compare            # print the table
    python -m scripts.loc_compare --write    # also refresh the table in COMPARISON.md
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TS_ROOT = ROOT / "vendor" / "telemetry-ops-copilot"
PY = "src/incident_commander/"

# component: (TypeScript files, Python files). Same responsibility on each side.
COMPONENTS: dict[str, tuple[list[str], list[str]]] = {
    "Orchestration (loop or graph, state, routing, prompts)": (
        ["src/agent/loop.ts", "src/agent/prompt.ts"],
        [
            PY + f
            for f in (
                "graph.py",
                "state.py",
                "routing.py",
                "prompts.py",
                "nodes/__init__.py",
                "nodes/triage.py",
                "nodes/investigator.py",
                "nodes/synthesizer.py",
                "nodes/budget.py",
                "nodes/postmortem.py",
            )
        ],
    ),
    "Human approval and write safety": (
        ["src/agent/approval.ts", "src/agent/http-approver.ts", "src/agent/guardrails.ts"],
        [PY + f for f in ("approvals.py", "actions.py", "nodes/approval.py", "nodes/executor.py", "ledger.py")],
    ),
    "Persistence, resume, time travel": (
        ["src/agent/run-store.ts", "src/agent/agent-db.ts", "src/agent/audit.ts"],
        [PY + f for f in ("checkpointer.py", "runner.py", "timetravel.py")],
    ),
    "Budgets and pricing": (["src/llm/pricing.ts"], [PY + "budgets.py", PY + "pricing.py"]),
    "Model providers (live + mock)": (
        ["src/llm/api-provider.ts", "src/llm/index.ts", "src/llm/mock.ts", "src/llm/replay.ts", "src/llm/types.ts"],
        [
            PY + f
            for f in (
                "models/__init__.py",
                "models/factory.py",
                "models/fake.py",
                "models/fake_rules.py",
                "models/schemas.py",
                "llm.py",
            )
        ],
    ),
    "MCP client and tool registry": (
        ["src/agent/mcp-client.ts", "src/tools/definitions.ts"],
        [PY + "mcp_bridge.py", PY + "registry.py"],
    ),
    "HTTP API and live streaming": (
        ["src/agent/http-api.ts", "src/agent/http-main.ts", "src/agent/live-runs.ts"],
        [PY + "api.py"],
    ),
    "Tracing": (["src/telemetry/tracer.ts", "src/telemetry/otlp.ts"], [PY + "tracing.py"]),
    "CLI and config": (
        ["src/agent/cli.ts", "src/agent/format.ts", "src/config.ts"],
        [PY + f for f in ("cli.py", "config.py", "paths.py", "timeutil.py", "__init__.py")],
    ),
    "Approval UI": (
        ["web/src/App.tsx", "web/src/api.ts", "web/src/main.tsx", "web/index.html"],
        [PY + "static/approve.html"],
    ),
    "Evals": (["evals/run.ts", "evals/score.ts", "evals/judge.ts"], ["evals/run.py", "evals/baseline.py"]),
}
# Different scope on each side (the TS repo owns the real data layer), so not in the total.
SERVERS = (
    ["src/server/mcp-server.ts", "src/server/http.ts", "src/server/http-main.ts", "src/server/stdio.ts",
     "src/data/anomalies.ts", "src/data/db.ts", "src/data/queries.ts", "src/data/runbooks.ts", "src/data/seed.ts"],
    ["servers/seed.py", "servers/telemetry_stub/server.py", "servers/telemetry_stub/anomalies.py",
     "servers/fleet_context/server.py"],
)  # fmt: skip


def python_loc(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    docstring_lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return sum(
        1
        for number, line in enumerate(source.splitlines(), start=1)
        if line.strip() and not line.strip().startswith("#") and number not in docstring_lines
    )


def typescript_loc(path: Path) -> int:
    count, in_block = 0, False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if in_block:
            in_block = "*/" not in line
            continue
        if line.startswith("/*"):
            in_block = "*/" not in line
            continue
        if line and not line.startswith("//"):
            count += 1
    return count


def loc(path: Path) -> int:
    if path.suffix == ".py":
        return python_loc(path)
    if path.suffix in (".ts", ".tsx"):
        return typescript_loc(path)
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def total(root: Path, files: list[str]) -> int:
    return sum(loc(root / f) for f in files)


def table() -> str:
    if not TS_ROOT.exists():
        raise SystemExit("vendor/telemetry-ops-copilot is missing: run scripts/fetch_telemetry.sh")
    rows = ["| component | hand-written loop (TS) | LangGraph (Python) |", "| --- | --- | --- |"]
    ts_sum = py_sum = 0
    for name, (ts_files, py_files) in COMPONENTS.items():
        ts, py = total(TS_ROOT, ts_files), total(ROOT, py_files)
        ts_sum, py_sum = ts_sum + ts, py_sum + py
        rows.append(f"| {name} | {ts} | {py} |")
    rows.append(f"| **total (agent side)** | **{ts_sum}** | **{py_sum}** |")
    rows.append(f"| MCP servers and data (not in total) | {total(TS_ROOT, SERVERS[0])} | {total(ROOT, SERVERS[1])} |")
    return "\n".join(rows)


def write_comparison() -> None:
    """Replace the table between the loc markers in COMPARISON.md with a fresh count."""
    path = ROOT / "COMPARISON.md"
    text = path.read_text(encoding="utf-8")
    start, end = text.index("<!-- loc:start -->\n"), text.index("\n<!-- loc:end -->")
    path.write_text(text[: start + len("<!-- loc:start -->\n")] + table() + text[end:], encoding="utf-8")


if __name__ == "__main__":
    print(table())
    if "--write" in sys.argv:
        write_comparison()
