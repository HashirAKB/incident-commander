"""The graph's state: one TypedDict that the checkpointer saves after every step.

Everything here is plain JSON (dicts, lists, strings, numbers). That keeps
checkpoints readable in `ic history` and safe to load in a new process: no
custom classes need to be importable to restore a run.

Two fields use reducers, because several nodes write them in parallel:
- findings: merged by investigator name (see merge_findings)
- usage:    summed (see sum_usage)
Every other field is "last write wins", and only one node writes each.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

Category = Literal["thermal", "power", "connectivity", "mechanical", "config_regression", "unknown"]
RootCause = Literal["thermal", "power", "connectivity", "mechanical", "config_regression", "unknown", "no_fault"]
Severity = Literal["low", "medium", "high", "critical"]
Verdict = Literal["approve", "edit", "reject", "expired"]
StopReason = Literal[
    "completed",
    "no_action_needed",
    "rejected",
    "expired",
    "step_limit",
    "token_limit",
    "cost_limit",
    "error",
]
InvestigatorName = Literal["metrics", "events", "runbooks", "config_changes"]
INVESTIGATORS: tuple[InvestigatorName, ...] = ("metrics", "events", "runbooks", "config_changes")


class Alert(TypedDict):
    alert_id: str
    target: str  # a device id ("D-17") or "fleet"
    metric: str  # temperature | battery | signal | vibration | other
    summary: str
    fired_at: str  # ISO 8601 UTC
    site: NotRequired[str]


class Limits(TypedDict):
    max_steps: int
    max_tokens: int
    max_cost_usd: float


class Usage(TypedDict):
    steps: int  # one step = one model call or one tool call
    input_tokens: int
    output_tokens: int
    cost_usd: float


class Triage(TypedDict):
    category: Category
    severity: Severity
    investigators: list[InvestigatorName]
    rationale: str


class Finding(TypedDict):
    finding_id: str  # "F-metrics", stable so evidence can reference it
    investigator: InvestigatorName
    summary: str
    tool_calls: list[str]  # "detect_anomalies(target=D-17,...)" in call order
    blocked_tool_calls: list[str]  # calls the node refused (e.g. a WRITE tool)
    truncated: bool  # stopped early by the tool-call cap or the branch budget


class Diagnosis(TypedDict):
    root_cause: RootCause
    confidence: float
    summary: str
    evidence: list[str]  # finding ids


class ProposedAction(TypedDict):
    action_id: str  # "A1", "A2", ... assigned by the synthesizer node, never by the model
    tool: str  # a WRITE tool name from docs/mcp-contract.md
    args: dict[str, Any]
    rationale: str
    risk: str


class PendingAction(ProposedAction):
    args_hash: str  # sha256 of the canonical (tool, args); binds a decision to these exact args


class ApprovalRequest(TypedDict):
    approval_id: str
    requested_at: str
    expires_at: str
    actions: list[PendingAction]


class Decision(TypedDict):
    action_id: str
    verdict: Verdict
    args: dict[str, Any] | None  # the args to execute (edited or original); None unless approved
    note: str


class Execution(TypedDict):
    action_id: str
    tool: str
    args: dict[str, Any]
    status: Literal["executed", "skipped_duplicate", "failed"]
    result: str


def merge_findings(left: dict[str, Finding] | None, right: dict[str, Finding] | None) -> dict[str, Finding]:
    """Reducer for parallel investigators.

    Keyed by investigator name, so a branch that runs twice (resume after a
    crash, replay from a checkpoint) replaces its own finding instead of
    appending a duplicate. Sorted by key so the merged dict has the same order
    no matter which branch finished first.
    """
    merged = {**(left or {}), **(right or {})}
    return dict(sorted(merged.items()))


EMPTY_USAGE: Usage = {"steps": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def sum_usage(left: Usage | None, right: Usage | None) -> Usage:
    """Reducer: parallel branches each report what they spent; the totals add up."""
    a, b = left or EMPTY_USAGE, right or EMPTY_USAGE
    return {
        "steps": a["steps"] + b["steps"],
        "input_tokens": a["input_tokens"] + b["input_tokens"],
        "output_tokens": a["output_tokens"] + b["output_tokens"],
        # Rounded so float noise never makes two identical runs differ.
        "cost_usd": round(a["cost_usd"] + b["cost_usd"], 8),
    }


class IncidentState(TypedDict):
    # Inputs, set once by the caller (runner.start_run).
    alert: Alert
    mode: Literal["mock", "live"]
    started_at: str
    limits: Limits
    approval_ttl_seconds: int
    # Written by nodes.
    triage: NotRequired[Triage]
    findings: Annotated[dict[str, Finding], merge_findings]
    usage: Annotated[Usage, sum_usage]
    diagnosis: NotRequired[Diagnosis]
    proposed_actions: NotRequired[list[ProposedAction]]
    approval_request: NotRequired[ApprovalRequest]
    decisions: NotRequired[dict[str, Decision]]
    executions: NotRequired[list[Execution]]
    stop_reason: NotRequired[StopReason]
    error: NotRequired[str]
    postmortem_path: NotRequired[str]


class InvestigatorInput(TypedDict):
    """What each Send() hands to one investigator branch."""

    investigator: InvestigatorName
    alert: Alert
    triage: Triage
    branch_budget: Limits  # this branch's share of the remaining run budget
