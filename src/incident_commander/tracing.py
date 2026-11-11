"""OpenTelemetry spans for nodes, model calls and tool calls, exported as JSONL.

One file per run: traces/<thread_id>.jsonl, one span per line. No collector or
backend is needed; `ic trace <thread_id>` prints a timeline from the file.

Run labels (thread_id, mode) live in a ContextVar set by the runner, so every
span gets them without passing them through every function.

Secrets: span attributes pass through `redact()`, and live prompts are never
recorded, only token counts. LangSmith is enabled only by its own environment
variables (LANGSMITH_API_KEY + LANGSMITH_TRACING) and is off otherwise.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import NoOpTracer, Span, Tracer

from incident_commander.paths import validate_thread_id
from incident_commander.state import Usage

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
]
SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "APPROVER_TOKEN", "LANGSMITH_API_KEY", "DATABASE_URL", "TELEMETRY_MCP_TOKEN")


@dataclass(frozen=True)
class RunLabels:
    thread_id: str
    mode: str


current_run: ContextVar[RunLabels | None] = ContextVar("current_run", default=None)
_tracer: Tracer = NoOpTracer()


def redact(value: str) -> str:
    for name in SECRET_ENV_VARS:
        secret = os.environ.get(name)
        if secret and len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


class JsonlSpanExporter(SpanExporter):
    """Appends finished spans to <traces_dir>/<thread_id>.jsonl."""

    def __init__(self, traces_dir: Path) -> None:
        self.traces_dir = traces_dir

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            attributes = dict(span.attributes or {})
            thread_id = str(attributes.get("thread_id", ""))
            if not thread_id:
                continue  # spans outside a run (e.g. tests) are dropped
            path = self.traces_dir / f"{validate_thread_id(thread_id)}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            context = span.get_span_context()
            record = {
                "name": span.name,
                "trace_id": f"{context.trace_id:032x}" if context else None,
                "span_id": f"{context.span_id:016x}" if context else None,
                "parent_id": f"{span.parent.span_id:016x}" if span.parent else None,
                "start_ns": span.start_time,
                "end_ns": span.end_time,
                "status": span.status.status_code.name,
                "attributes": {k: redact(v) if isinstance(v, str) else v for k, v in attributes.items()},
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        return SpanExportResult.SUCCESS


def init_tracing(traces_dir: Path) -> None:
    """Turn on span export for this process. Safe to call more than once."""
    global _tracer
    provider = TracerProvider()
    # Simple (synchronous) processor: a span is on disk as soon as it ends,
    # so a SIGKILL loses at most the span that was open.
    provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(traces_dir)))
    _tracer = provider.get_tracer("incident-commander")


@contextmanager
def _span(name: str, attributes: dict[str, Any]) -> Iterator[Span]:
    labels = current_run.get()
    if labels:
        attributes = {"thread_id": labels.thread_id, "mode": labels.mode, **attributes}
    with _tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span


def node_span(node: str) -> Any:
    return _span(f"node {node}", {"kind": "node", "node": node})


def model_span(node: str, mode: str) -> Any:
    return _span(f"model {node}", {"kind": "model", "node": node, "mode": mode})


def tool_span(node: str, tool: str) -> Any:
    return _span(f"tool {tool}", {"kind": "tool", "node": node, "tool": tool})


def record_usage(span: Span, usage: Usage) -> None:
    span.set_attribute("tokens.input", usage["input_tokens"])
    span.set_attribute("tokens.output", usage["output_tokens"])
    span.set_attribute("cost_usd", usage["cost_usd"])


def read_trace(traces_dir: Path, thread_id: str) -> list[dict[str, Any]]:
    path = traces_dir / f"{validate_thread_id(thread_id)}.jsonl"
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return sorted(records, key=lambda r: (r["start_ns"], r["end_ns"]))


def format_timeline(records: list[dict[str, Any]]) -> list[str]:
    """Text timeline for `ic trace`: offset from the first span, duration, name, key attributes."""
    if not records:
        return []
    start = records[0]["start_ns"]
    lines = []
    for record in records:
        attributes = record["attributes"]
        offset_ms = (record["start_ns"] - start) / 1e6
        duration_ms = (record["end_ns"] - record["start_ns"]) / 1e6
        extras = [
            f"{key}={attributes[key]}"
            for key in ("tokens.input", "tokens.output", "cost_usd", "stop_reason")
            if key in attributes
        ]
        if attributes.get("tool.error"):
            extras.append("error")
        indent = "  " if record["parent_id"] else ""
        lines.append(f"{offset_ms:9.1f}ms {duration_ms:8.1f}ms  {indent}{record['name']}  {' '.join(extras)}".rstrip())
    return lines


def configure_langsmith() -> bool:
    """LangSmith tracing is on if and only if LANGSMITH_API_KEY is set.

    LangChain turns LangSmith on by itself when LANGSMITH_TRACING=true, so we set
    that variable explicitly either way. When on, LangSmith receives prompts and
    tool output; it is an opt-in for live debugging, off by default.
    """
    enabled = bool(os.environ.get("LANGSMITH_API_KEY"))
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    return enabled
