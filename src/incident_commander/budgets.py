"""Run budgets: steps, tokens and dollars, checked at node boundaries.

Parallel investigators cannot see each other's spending while they run, so
each branch gets an equal share of what is left (remaining / branches). The
tradeoff: a cheap branch cannot lend its unused share to an expensive one.
The alternative, a shared counter, would need locking across branches and
would make results depend on which branch happened to run first.
"""

from __future__ import annotations

from incident_commander.state import Limits, StopReason, Usage


def exceeded(usage: Usage, limits: Limits) -> StopReason | None:
    """The first limit that usage has reached, or None. Checked in a fixed order."""
    if usage["steps"] >= limits["max_steps"]:
        return "step_limit"
    if usage["input_tokens"] + usage["output_tokens"] >= limits["max_tokens"]:
        return "token_limit"
    if usage["cost_usd"] >= limits["max_cost_usd"]:
        return "cost_limit"
    return None


def remaining(usage: Usage, limits: Limits) -> Limits:
    return {
        "max_steps": max(0, limits["max_steps"] - usage["steps"]),
        "max_tokens": max(0, limits["max_tokens"] - usage["input_tokens"] - usage["output_tokens"]),
        "max_cost_usd": max(0.0, round(limits["max_cost_usd"] - usage["cost_usd"], 8)),
    }


def split(budget: Limits, branches: int) -> Limits:
    """Equal share per parallel branch, rounded down so the shares never sum above the total."""
    if branches < 1:
        raise ValueError("branches must be >= 1")
    return {
        "max_steps": budget["max_steps"] // branches,
        "max_tokens": budget["max_tokens"] // branches,
        "max_cost_usd": round(budget["max_cost_usd"] / branches, 8),
    }
