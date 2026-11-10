#!/usr/bin/env bash
# Fetch the real telemetry MCP server at a pinned commit into vendor/ (gitignored).
# Needs git, Node 20+ and npm. The project works without it (the Python stub is used).
set -euo pipefail

REPO_URL="https://github.com/HashirAKB/telemetry-ops-copilot"
PINNED_SHA="2cf208c5183a3077358c6d628fa68bcbf1d6205f"
TARGET="vendor/telemetry-ops-copilot"

if [ ! -d "$TARGET/.git" ]; then
  git init -q "$TARGET"
  git -C "$TARGET" remote add origin "$REPO_URL"
fi
git -C "$TARGET" fetch -q --depth 1 origin "$PINNED_SHA"
git -C "$TARGET" checkout -q --detach "$PINNED_SHA"
(cd "$TARGET" && npm ci --no-audit --no-fund --loglevel=error)
# The stdio server opens the database but does not seed it, so seed once here.
(cd "$TARGET" && DB_PATH=data/fleet.db npm run --silent seed)
echo "telemetry server ready at $TARGET ($PINNED_SHA)"
