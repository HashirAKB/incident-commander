# Two images from one file:
#   target "telemetry": the real telemetry-ops-copilot MCP server at the pinned SHA (HTTP on :3001)
#   target "app":       incident-commander API + approval page (HTTP on :8000)

# ---------------------------------------------------------------- telemetry
FROM node:22-slim AS telemetry
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY scripts/fetch_telemetry.sh scripts/fetch_telemetry.sh
RUN ./scripts/fetch_telemetry.sh
WORKDIR /build/vendor/telemetry-ops-copilot
ENV MCP_PORT=3001 DB_PATH=data/fleet.db
EXPOSE 3001
# MCP_API_TOKEN must be provided at runtime; the server then requires it on every request.
CMD ["node_modules/.bin/tsx", "src/server/http-main.ts"]

# ---------------------------------------------------------------------- app
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src src
COPY servers servers
COPY config config
COPY docs/mcp-contract.md docs/mcp-contract.md
RUN uv sync --frozen --no-dev
RUN useradd --create-home appuser && mkdir -p data runs traces && chown -R appuser /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH" IC_MODE=mock
EXPOSE 8000
# Binding 0.0.0.0 inside the container is refused unless APPROVER_TOKEN is set.
CMD ["ic", "serve", "--host", "0.0.0.0", "--port", "8000"]
