"""Token prices from config/pricing.toml and the cost of one model call."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from incident_commander.config import Mode

PRICING_PATH = Path(__file__).resolve().parents[2] / "config" / "pricing.toml"


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float


@cache
def _table() -> dict[str, object]:
    return tomllib.loads(PRICING_PATH.read_text(encoding="utf-8"))


def price_for(mode: Mode, model_name: str) -> Price:
    table = _table()
    if mode == "mock":
        mock = table["mock"]
        assert isinstance(mock, dict)
        return Price(float(mock["input_per_mtok"]), float(mock["output_per_mtok"]))
    live = table["live"]
    assert isinstance(live, dict)
    models = live["models"]
    if model_name not in models:
        raise KeyError(f"No price for {model_name!r} in config/pricing.toml; add it before running live")
    entry = models[model_name]
    return Price(float(entry["input_per_mtok"]), float(entry["output_per_mtok"]))


def cost_usd(price: Price, input_tokens: int, output_tokens: int) -> float:
    return round((input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1_000_000, 8)
