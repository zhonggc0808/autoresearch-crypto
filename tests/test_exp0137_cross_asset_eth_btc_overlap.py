from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0137 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.py"


def load_exp0137():
    spec = importlib.util.spec_from_file_location("exp0137_eth_btc_overlap", EXP0137)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0137_eth_btc_overlap"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(close: list[float], start: str = "2026-01-01", freq: str = "5min") -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range(start, periods=len(values), freq=freq),
            "open": values,
            "high": values,
            "low": values,
            "close": values,
            "volume": np.full(len(values), 100.0),
        }
    )


def test_btc_core_donchian_uses_shifted_channel() -> None:
    exp0137 = load_exp0137()
    df = ohlcv([10.0, 11.0, 12.0, 13.5, 7.0, 6.0])

    signals = exp0137.core_donchian_signals(df, entry_lookback=3, min_hold_bars=2)

    assert signals.tolist() == [1, 1, 1, 2, 2, 3]


def test_completed_bar_signal_shifts_to_next_open() -> None:
    exp0137 = load_exp0137()

    shifted = exp0137.shifted_next_open_signals(np.asarray([2, 2, 3, 1], dtype=int))

    assert shifted.tolist() == [1, 2, 2, 3]


def test_overlap_window_slices_both_assets_to_same_bounds() -> None:
    exp0137 = load_exp0137()
    left = ohlcv([1.0, 2.0, 3.0, 4.0], start="2026-01-01")
    right = ohlcv([5.0, 6.0, 7.0, 8.0], start="2026-01-01 00:05")
    signals = np.asarray([1, 2, 2, 3], dtype=int)

    start, end = exp0137.overlap_bounds(left, right)
    sliced, sliced_signals = exp0137.slice_by_time_range(left, signals, start, end)

    assert start == pd.Timestamp("2026-01-01 00:05")
    assert end == pd.Timestamp("2026-01-01 00:15")
    assert sliced["datetime"].tolist() == list(pd.date_range("2026-01-01 00:05", periods=3, freq="5min"))
    assert sliced_signals.tolist() == [2, 2, 3]


def test_daily_return_alignment_drops_missing_days_without_ffill() -> None:
    exp0137 = load_exp0137()
    left = pd.Series([100.0, 110.0, 121.0], index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
    right = pd.Series([100.0, 90.0], index=pd.to_datetime(["2026-01-01", "2026-01-03"]))

    frame = exp0137.aligned_daily_frame_no_fill({"left": left, "right": right})

    assert frame.index.tolist() == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-03")]
    assert pd.Timestamp("2026-01-02") not in frame.index


def test_combo_weight_sum_is_validated() -> None:
    exp0137 = load_exp0137()

    exp0137.validate_combo_weights({"a": 0.8, "b": 0.2})
    with pytest.raises(ValueError):
        exp0137.validate_combo_weights({"a": 0.8, "b": 0.1})


def test_dd_relative_improvement_formula() -> None:
    exp0137 = load_exp0137()

    assert exp0137.dd_improve_ratio(-0.50, -0.40) == pytest.approx(0.20)
    assert exp0137.dd_improve_ratio(-0.50, -0.55) == pytest.approx(-0.10)


def test_sol_is_not_in_first_round_scope() -> None:
    exp0137 = load_exp0137()

    assert "SOLUSDT" not in exp0137.ASSETS
    assert all("SOL" not in combo for combo, _ in exp0137.COMBO_SPECS)


def test_verdict_is_consistent_with_stage_gates() -> None:
    exp0137 = load_exp0137()

    assert exp0137.verdict_from_stage_gates([{"passes_combo_gate": True, "passes_observe_gate": True}]) == "SHADOW_CANDIDATE"
    assert exp0137.verdict_from_stage_gates([{"passes_combo_gate": False, "passes_observe_gate": True}]) == "OBSERVE"
    assert exp0137.verdict_from_stage_gates([{"passes_combo_gate": False, "passes_observe_gate": False}]) == "REJECT"
