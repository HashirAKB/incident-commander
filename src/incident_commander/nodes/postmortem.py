"""postmortem: decide the final stop_reason and write runs/<thread_id>/postmortem.md.

`render_postmortem` is a pure function of state, so the same run always gives
the same file (no clock reads here: times come from state).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from incident_commander import tracing
from incident_commander.nodes import StateConfigNode
from incident_commander.paths import run_dir
from incident_commander.state import IncidentState, StopReason

MOCK_NOTE = "Mock mode uses a deterministic fake model: this text shows the graph wiring, not model quality. "


def final_stop_reason(state: IncidentState) -> StopReason:
    """Keep a stop_reason an earlier node set (budget, rejected, expired); otherwise derive one."""
    if "stop_reason" in state:
        return state["stop_reason"]
    return "completed" if state.get("proposed_actions") else "no_action_needed"


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "- none"


def _args(args: dict[str, Any] | None) -> str:
    return f"`{json.dumps(args, sort_keys=True)}`" if args is not None else "-"


def render_postmortem(state: IncidentState, thread_id: str, stop_reason: StopReason) -> str:
    alert, mode = state["alert"], state["mode"]
    triage = state.get("triage")
    diagnosis = state.get("diagnosis")
    findings = state.get("findings", {})
    actions = state.get("proposed_actions", [])
    decisions = state.get("decisions", {})
    executions = state.get("executions", [])
    request = state.get("approval_request")
    usage = state["usage"]

    timeline = [f"{state['started_at']}: alert `{alert['alert_id']}` received for `{alert['target']}`"]
    if triage:
        timeline.append(
            f"triage: {triage['category']} / {triage['severity']}; ran {', '.join(triage['investigators'])}"
        )
    if findings:
        timeline.append(f"investigators finished: {', '.join(findings)}")
    if diagnosis:
        timeline.append(f"synthesizer: root cause {diagnosis['root_cause']}, {len(actions)} action(s) proposed")
    if request:
        timeline.append(f"{request['requested_at']}: approval requested (`{request['approval_id']}`)")
        timeline.append(f"approval deadline: {request['expires_at']}")
    if decisions:
        verdicts = ", ".join(f"{d['action_id']}={d['verdict']}" for d in decisions.values())
        timeline.append(f"decisions: {verdicts}")
    if executions:
        timeline.append(f"executor: {', '.join(f'{e["action_id"]}={e["status"]}' for e in executions)}")
    timeline.append(f"stopped: {stop_reason}")

    sections = [
        f"# DRAFT postmortem: {alert['summary']}",
        "",
        f"> DRAFT generated in **mode={mode}**. "
        + (MOCK_NOTE if mode == "mock" else "")
        + "Review every statement before sharing.",
        "",
        "| field | value |",
        "| --- | --- |",
        f"| thread_id | `{thread_id}` |",
        f"| alert | `{alert['alert_id']}` on `{alert['target']}` ({alert['metric']}) |",
        f"| fired_at | {alert['fired_at']} |",
        f"| stop_reason | **{stop_reason}** |",
        f"| usage | {usage['steps']} steps, {usage['input_tokens'] + usage['output_tokens']} tokens, "
        f"${usage['cost_usd']:.6f} (mode={mode}{', fake prices' if mode == 'mock' else ''}) |",
        "",
        "## Timeline",
        "",
        _bullets(timeline),
        "",
        "## Findings",
        "",
    ]
    for finding in findings.values():
        sections += [
            f"### {finding['finding_id']} ({finding['investigator']}){' [truncated]' if finding['truncated'] else ''}",
            "",
            "Tool calls: " + (", ".join(f"`{c}`" for c in finding["tool_calls"]) or "none"),
            "",
        ]
        if finding["blocked_tool_calls"]:
            sections += ["Blocked tool calls: " + ", ".join(f"`{c}`" for c in finding["blocked_tool_calls"]), ""]
        sections += ["```text", finding["summary"], "```", ""]
    if not findings:
        sections += ["none", ""]

    sections += ["## Diagnosis", ""]
    if diagnosis:
        sections += [
            f"- root cause: **{diagnosis['root_cause']}** (confidence {diagnosis['confidence']:.2f})",
            f"- summary: {diagnosis['summary']}",
            f"- evidence: {', '.join(diagnosis['evidence']) or 'none'}",
            "",
        ]
    else:
        sections += ["not reached", ""]

    sections += [
        "## Proposed actions",
        "",
        "| id | tool | args | decision | executed |",
        "| --- | --- | --- | --- | --- |",
    ]
    executed = {e["action_id"]: e["status"] for e in executions}
    for action in actions:
        decision = decisions.get(action["action_id"])
        sections.append(
            f"| {action['action_id']} | {action['tool']} | {_args(action['args'])} | "
            f"{decision['verdict'] if decision else '-'} | {executed.get(action['action_id'], 'no')} |"
        )
    if not actions:
        sections.append("| - | - | - | - | - |")
    sections += ["", "Dropped proposals (failed validation):", "", _bullets(state.get("dropped_proposals", [])), ""]

    sections += ["## Executed actions", ""]
    sections += [_bullets([f"{e['action_id']} {e['tool']} {_args(e['args'])}: {e['status']}" for e in executions]), ""]
    if state.get("error"):
        sections += ["## Error", "", f"```text\n{state['error']}\n```", ""]
    return "\n".join(sections)


def write_postmortem(runs_root: Path, thread_id: str, content: str) -> Path:
    directory = run_dir(runs_root, thread_id)  # validates thread_id before any filesystem access
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "postmortem.md"
    path.write_text(content, encoding="utf-8")
    return path


def make_postmortem(runs_root: Path) -> StateConfigNode:
    async def postmortem(state: IncidentState, config: RunnableConfig) -> dict[str, Any]:
        with tracing.node_span("postmortem") as span:
            thread_id = str(config["configurable"]["thread_id"])
            stop_reason = final_stop_reason(state)
            path = write_postmortem(runs_root, thread_id, render_postmortem(state, thread_id, stop_reason))
            span.set_attribute("stop_reason", stop_reason)
            return {"stop_reason": stop_reason, "postmortem_path": str(path)}

    return postmortem
