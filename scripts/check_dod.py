"""Check the Definition of Done items that a script can check.

    python -m scripts.check_dod                 # check
    python -m scripts.check_dod --sync-readme   # first copy the mock eval table into README.md

- every required file exists
- no em dash character in README.md or SUMMARY.md
- the README eval table equals the one in evals/results/mock.md
- COMPARISON.md's LOC table equals a fresh run of scripts/loc_compare.py
- COMPARISON.md's latency table equals the committed docs/latency.json
- .env.example carries no secret values
Exits 1 and lists every problem if anything fails.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from scripts import latency_compare, loc_compare

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE", ".env.example", "COMPARISON.md", "LEARNING.md",
    "SUMMARY.md", "SPEC.md", "PLAN.md", "DECISIONS.md", "docs/mcp-contract.md", "evals/run.py",
    "evals/baseline.py", "evals/cases.json", "evals/results/mock.md", "evals/results/mock.json", "Dockerfile",
    "docker-compose.yml", ".github/workflows/ci.yml", ".github/workflows/live-eval.yml",
    "docs/screenshots/cli.txt", "docs/screenshots/sse.txt", "docs/screenshots/approval_http.txt",
    "docs/latency.json", "tests/live/test_smoke.py",
]  # fmt: skip
SECRET_KEYS = ("ANTHROPIC_API_KEY", "APPROVER_TOKEN", "LANGSMITH_API_KEY", "TELEMETRY_MCP_TOKEN", "DATABASE_URL")
EM_DASH = "—"


def block(text: str, name: str) -> str:
    match = re.search(rf"<!-- {name}:start -->\n(.*?)\n<!-- {name}:end -->", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def mock_eval_table() -> str:
    lines = (ROOT / "evals/results/mock.md").read_text().splitlines()
    start = lines.index("| metric | graph | baseline (create_react_agent) |")
    end = lines.index("## Per case")
    return "\n".join(lines[start:end]).strip()


def problems() -> list[str]:
    found = [f"missing {path}" for path in REQUIRED if not (ROOT / path).exists()]
    adrs = list((ROOT / "docs/adr").glob("*.md"))
    if not 3 <= len(adrs) <= 5:
        found.append(f"docs/adr has {len(adrs)} ADRs, expected 3 to 5")
    if (ROOT / ".claude").exists() or (ROOT / "CLAUDE.md").exists():
        found.append(".claude/ or CLAUDE.md must not exist in this repo")
    for name in ("README.md", "SUMMARY.md"):
        path = ROOT / name
        if path.exists() and EM_DASH in path.read_text():
            found.append(f"{name} contains an em dash")
    readme = (ROOT / "README.md").read_text() if (ROOT / "README.md").exists() else ""
    if block(readme, "eval") != mock_eval_table():
        found.append("README eval table differs from evals/results/mock.md")
    comparison = (ROOT / "COMPARISON.md").read_text() if (ROOT / "COMPARISON.md").exists() else ""
    if loc_compare.TS_ROOT.exists():
        if block(comparison, "loc") != loc_compare.table().strip():
            found.append("COMPARISON.md LOC table differs from scripts/loc_compare.py output")
    else:
        print("note: vendor/telemetry-ops-copilot missing, LOC table not re-checked")
    latency = latency_compare.render(json.loads(latency_compare.RESULT_PATH.read_text()))
    if block(comparison, "latency") != latency.strip():
        found.append("COMPARISON.md latency table differs from docs/latency.json")
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, _, value = line.partition("=")
        if key in SECRET_KEYS and value.strip():
            found.append(f".env.example sets a value for {key}")
    return found


def sync_readme() -> None:
    """Copy the aggregate table from evals/results/mock.md into the README markers."""
    path = ROOT / "README.md"
    text = path.read_text()
    start, end = text.index("<!-- eval:start -->\n"), text.index("\n<!-- eval:end -->")
    path.write_text(text[: start + len("<!-- eval:start -->\n")] + mock_eval_table() + text[end:])


def main() -> int:
    if "--sync-readme" in sys.argv:
        sync_readme()
    found = problems()
    for problem in found:
        print(f"FAIL: {problem}")
    if not found:
        print("Definition of Done checks passed")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
