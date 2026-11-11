"""All configuration comes from environment variables, with safe local defaults.

Read once per process with `load_settings()`. Nothing here is secret except the
tokens, which are kept out of state, traces and logs (see tracing.py).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["mock", "live"]
TelemetryBackend = Literal["auto", "stub", "real"]

DEFAULT_MODEL = "claude-sonnet-5-5"
VENDOR_TELEMETRY_DIR = Path("vendor/telemetry-ops-copilot")


@dataclass(frozen=True)
class Settings:
    mode: Mode
    anthropic_model: str
    database_url: str | None
    data_dir: Path
    runs_dir: Path
    traces_dir: Path
    approval_ttl_seconds: int
    approver_token: str | None
    telemetry_backend: TelemetryBackend
    telemetry_url: str | None  # MCP over streamable HTTP (e.g. the compose service); overrides the backend
    telemetry_token: str | None
    write_log: Path
    max_steps: int
    max_tokens: int
    max_cost_usd: float

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"


def parse_duration(text: str) -> int:
    """'4h', '30m', '90s' or plain seconds -> seconds."""
    match = re.fullmatch(r"\s*(\d+)\s*([hms]?)\s*", text)
    if match is None:
        raise ValueError(f"Invalid duration {text!r}; use e.g. 4h, 30m, 90s or seconds")
    number, unit = int(match.group(1)), match.group(2) or "s"
    return number * {"h": 3600, "m": 60, "s": 1}[unit]


def _mode() -> Mode:
    # The live model is used only when a key exists. IC_MODE=mock forces the
    # fake model even then (tests and evals use this to stay deterministic).
    if os.environ.get("IC_MODE") == "mock" or not os.environ.get("ANTHROPIC_API_KEY"):
        return "mock"
    return "live"


def _telemetry_backend() -> TelemetryBackend:
    value = os.environ.get("IC_TELEMETRY", "auto")
    if value not in ("auto", "stub", "real"):
        raise ValueError("IC_TELEMETRY must be auto, stub or real")
    return value  # type: ignore[return-value]


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("IC_DATA_DIR", "data"))
    return Settings(
        mode=_mode(),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        database_url=os.environ.get("DATABASE_URL") or None,
        data_dir=data_dir,
        runs_dir=Path(os.environ.get("IC_RUNS_DIR", "runs")),
        traces_dir=Path(os.environ.get("IC_TRACES_DIR", "traces")),
        approval_ttl_seconds=parse_duration(os.environ.get("APPROVAL_TTL", "4h")),
        approver_token=os.environ.get("APPROVER_TOKEN") or None,
        telemetry_backend=_telemetry_backend(),
        telemetry_url=os.environ.get("IC_TELEMETRY_URL") or None,
        telemetry_token=os.environ.get("TELEMETRY_MCP_TOKEN") or None,
        write_log=Path(os.environ.get("IC_WRITE_LOG", str(data_dir / "stub-writes.jsonl"))),
        max_steps=int(os.environ.get("IC_MAX_STEPS", "40")),
        max_tokens=int(os.environ.get("IC_MAX_TOKENS", "60000")),
        max_cost_usd=float(os.environ.get("IC_MAX_COST_USD", "0.50")),
    )
