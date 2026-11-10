"""Robust anomaly detection, a direct port of src/data/anomalies.ts.

zscore: distance from the median in robust sigmas (1.4826 * MAD); flags runs
of at least 3 points above 3.5 sigmas. ewma: smooth first, then flag drifts
above 3 sigmas, which catches slow ramps that single points never show.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Literal

Method = Literal["zscore", "ewma"]

ZSCORE_THRESHOLD, ZSCORE_MIN_RUN = 3.5, 3
EWMA_ALPHA, EWMA_THRESHOLD, EWMA_MIN_RUN = 0.1, 3.0, 1
MAX_GAP = 30  # runs closer than this many points are one event


@dataclass(frozen=True)
class AnomalyEvent:
    start_index: int
    end_index: int
    peak_value: float
    direction: Literal["high", "low"]
    score: float


def _scores(values: list[float], method: Method) -> tuple[list[float], float, int]:
    center = median(values)
    sigma = max(1.4826 * median(abs(v - center) for v in values), 1e-6)
    if method == "zscore":
        return [(v - center) / sigma for v in values], ZSCORE_THRESHOLD, ZSCORE_MIN_RUN
    smoothed, s = [], center
    for v in values:
        s = EWMA_ALPHA * v + (1 - EWMA_ALPHA) * s
        smoothed.append((s - center) / sigma)
    return smoothed, EWMA_THRESHOLD, EWMA_MIN_RUN


def detect(values: list[float], method: Method) -> list[AnomalyEvent]:
    if len(values) < 10:
        return []
    scores, threshold, min_run = _scores(values, method)

    runs: list[list[int]] = []
    run_start = -1
    for i in range(len(scores) + 1):
        flagged = i < len(scores) and abs(scores[i]) > threshold
        if flagged and run_start == -1:
            run_start = i
        if not flagged and run_start != -1:
            if i - run_start >= min_run:
                runs.append([run_start, i - 1])
            run_start = -1

    merged: list[list[int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= MAX_GAP:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    events = []
    for start, end in merged:
        peak = max(range(start, end + 1), key=lambda j: abs(scores[j]))
        events.append(
            AnomalyEvent(
                start_index=start,
                end_index=end,
                peak_value=values[peak],
                direction="high" if scores[peak] > 0 else "low",
                score=round(abs(scores[peak]), 1),
            )
        )
    return events
