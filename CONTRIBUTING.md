# Contributing

## Setup

```bash
uv sync
```

Optional: `./scripts/fetch_telemetry.sh` vendors the real telemetry server (needs Node 20+) so the
contract and crash tests also run against it.

## Checks (all must pass before a push)

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

```bash
uv run mypy
```

```bash
uv run pytest -q
```

```bash
uv run python -m evals.run --mode mock && git diff --exit-code evals/results/
```

Postgres variants run when `DATABASE_URL` points at a Postgres 16 database: `uv run pytest -q -m postgres`.

## Commit style

Imperative subject under about 72 characters, prefixed with the milestone or area when useful
(`M4: ...`, `docs: ...`, `fix: ...`), and a body that says why. One logical change per commit.
Keep docs and code in the same commit when the change needs both.
