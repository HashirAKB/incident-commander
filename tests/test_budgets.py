from __future__ import annotations

from incident_commander.budgets import exceeded, remaining, split
from incident_commander.pricing import cost_usd, price_for
from incident_commander.state import Limits, Usage, merge_findings, sum_usage

LIMITS: Limits = {"max_steps": 10, "max_tokens": 1000, "max_cost_usd": 0.01}


def usage(steps: int = 0, tokens: int = 0, cost: float = 0.0) -> Usage:
    return {"steps": steps, "input_tokens": tokens, "output_tokens": 0, "cost_usd": cost}


def test_within_budget() -> None:
    assert exceeded(usage(9, 999, 0.0099), LIMITS) is None


def test_each_limit_has_its_own_stop_reason() -> None:
    assert exceeded(usage(steps=10), LIMITS) == "step_limit"
    assert exceeded(usage(tokens=1000), LIMITS) == "token_limit"
    assert exceeded(usage(cost=0.01), LIMITS) == "cost_limit"


def test_steps_are_checked_first_when_several_limits_are_hit() -> None:
    assert exceeded(usage(10, 1000, 1.0), LIMITS) == "step_limit"


def test_remaining_never_negative() -> None:
    left = remaining(usage(20, 5000, 1.0), LIMITS)
    assert left == {"max_steps": 0, "max_tokens": 0, "max_cost_usd": 0.0}


def test_split_gives_each_branch_an_equal_share_rounded_down() -> None:
    share = split({"max_steps": 10, "max_tokens": 1001, "max_cost_usd": 0.03}, 3)
    assert share == {"max_steps": 3, "max_tokens": 333, "max_cost_usd": 0.01}
    assert share["max_steps"] * 3 <= 10


def test_usage_from_parallel_branches_sums() -> None:
    total = sum_usage(sum_usage(usage(1, 10, 0.1), usage(2, 20, 0.2)), usage(3, 30, 0.3))
    assert total == {"steps": 6, "input_tokens": 60, "output_tokens": 0, "cost_usd": 0.6}


def test_merge_findings_is_order_independent_and_never_duplicates() -> None:
    a = {"metrics": {"summary": "m"}}
    b = {"events": {"summary": "e"}}
    assert list(merge_findings(a, b)) == list(merge_findings(b, a)) == ["events", "metrics"]
    rerun = merge_findings(merge_findings(a, b), {"metrics": {"summary": "m2"}})  # type: ignore[arg-type]
    assert len(rerun) == 2 and rerun["metrics"]["summary"] == "m2"


def test_pricing_math_mock_and_live() -> None:
    mock = price_for("mock", "fake")
    assert cost_usd(mock, 1_000_000, 0) == mock.input_per_mtok
    live = price_for("live", "claude-sonnet-5-5")
    assert cost_usd(live, 1000, 1000) == round((1000 * 2.0 + 1000 * 10.0) / 1_000_000, 8)
