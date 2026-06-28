from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0138 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.py"


def load_exp0138():
    spec = importlib.util.spec_from_file_location("exp0138_position_sizing", EXP0138)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0138_position_sizing"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(
    close: list[float],
    *,
    open_: list[float] | None = None,
    high: list[float] | None = None,
    low: list[float] | None = None,
) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": np.asarray(open_ if open_ is not None else close, dtype=float),
            "high": np.asarray(high if high is not None else values, dtype=float),
            "low": np.asarray(low if low is not None else values, dtype=float),
            "close": values,
            "volume": np.full(len(values), 100.0),
        }
    )


def test_baseline_100_matches_existing_next_open_evaluator_for_simple_path() -> None:
    exp0138 = load_exp0138()
    df = ohlcv([100.0, 101.0, 102.0, 103.0, 104.0])
    signals = np.asarray([2, 2, 0, 1, 1], dtype=int)

    custom_equity, custom_trades, _, _ = exp0138.simulate_sizing(exp0138.VARIANTS[0], df, signals)
    base_equity, base_trades = exp0138.exp0136.evaluate_next_open(signals, df)

    assert custom_equity.tolist() == pytest.approx(base_equity.tolist())
    custom_closed = [trade for trade in custom_trades if trade.get("pnl") is not None]
    base_closed = [trade for trade in base_trades if trade.get("pnl") is not None]
    assert custom_closed[-1]["pnl"] == pytest.approx(base_closed[-1]["pnl"])


def test_reclaim_add_is_confirmed_on_completed_bar_and_executed_next_open() -> None:
    exp0138 = load_exp0138()
    df = ohlcv(
        [100.0, 101.0, 104.0, 105.0, 106.0],
        high=[100.0, 101.0, 104.0, 105.0, 106.0],
        low=[99.0, 100.0, 103.0, 104.0, 105.0],
    )
    signals = np.asarray([2, 2, 2, 2, 1], dtype=int)
    spec = exp0138.VariantSpec("test_add", 0.50, (0.25,), False, "reclaim_add_increment")

    _, _, events, exposure = exp0138.simulate_sizing(spec, df, signals, commission=0.0, slippage=0.0)
    add_events = [event for event in events if event["event"] == "add"]

    assert add_events
    assert add_events[0]["step"] == 3
    assert exposure[2] == pytest.approx(0.50)
    assert exposure[3] == pytest.approx(0.75)


def test_baseline_reversal_priority_blocks_same_bar_add() -> None:
    exp0138 = load_exp0138()
    df = ohlcv(
        [100.0, 101.0, 104.0, 103.0],
        high=[100.0, 101.0, 104.0, 103.0],
        low=[99.0, 100.0, 103.0, 102.0],
    )
    signals = np.asarray([2, 2, 3, 3], dtype=int)
    spec = exp0138.VariantSpec("test_reversal_priority", 0.50, (0.25,), False, "reclaim_add_increment")

    decisions = exp0138.make_decisions(spec, df, signals, commission=0.0, slippage=0.0)

    assert decisions[2]["kind"] == "target"
    assert decisions[2]["target"] == -1


def test_dd_throttle_scales_new_entry_after_realized_drawdown() -> None:
    exp0138 = load_exp0138()
    df = ohlcv(
        [100.0, 100.0, 60.0, 60.0, 61.0, 62.0],
        open_=[100.0, 100.0, 60.0, 60.0, 61.0, 62.0],
        high=[100.0, 100.0, 60.0, 61.0, 62.0, 63.0],
        low=[100.0, 100.0, 59.0, 60.0, 61.0, 62.0],
    )
    signals = np.asarray([2, 2, 0, 2, 2, 2], dtype=int)
    spec = exp0138.VariantSpec("test_throttle", 0.80, (), True, "linear_constant_control")

    decisions = exp0138.make_decisions(spec, df, signals, commission=0.0, slippage=0.0)

    assert decisions[3]["kind"] == "target"
    assert decisions[3]["target"] == 1
    assert decisions[3]["size"] < spec.initial_size


def test_exposure_cap_prevents_second_add_past_100pct() -> None:
    exp0138 = load_exp0138()
    df = ohlcv(
        [100.0, 101.0, 104.0, 106.0, 108.0, 110.0],
        high=[100.0, 101.0, 104.0, 106.0, 108.0, 110.0],
        low=[99.0, 100.0, 103.0, 105.0, 107.0, 109.0],
    )
    signals = np.asarray([2, 2, 2, 2, 2, 2], dtype=int)
    spec = exp0138.VariantSpec("test_cap", 0.75, (0.25, 0.25), False, "reclaim_add_increment")

    _, _, events, exposure = exp0138.simulate_sizing(spec, df, signals, commission=0.0, slippage=0.0)
    add_events = [event for event in events if event["event"] == "add"]

    assert len(add_events) == 1
    assert max(exposure) <= 1.0


def test_stage_verdict_mapping() -> None:
    exp0138 = load_exp0138()

    assert exp0138.verdict_from_gates([{"shadow_candidate": True, "observe": True}]) == "SHADOW_CANDIDATE"
    assert exp0138.verdict_from_gates([{"shadow_candidate": False, "observe": True}]) == "OBSERVE"
    assert exp0138.verdict_from_gates([{"shadow_candidate": False, "observe": False}]) == "REJECT"


def test_dd_throttle_tiers_are_wide_first_pass() -> None:
    exp0138 = load_exp0138()

    assert exp0138.dd_throttle_multiplier(0.10) == 1.0
    assert exp0138.dd_throttle_multiplier(0.20) == 0.75
    assert exp0138.dd_throttle_multiplier(0.30) == 0.50
    assert exp0138.dd_throttle_multiplier(0.45) == 0.25
