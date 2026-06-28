from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0136 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py"


def load_exp0136():
    spec = importlib.util.spec_from_file_location("exp0136_multitimeframe", EXP0136)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0136_multitimeframe"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(close: list[float], high: list[float] | None = None, low: list[float] | None = None) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": np.asarray(high if high is not None else values + 0.5, dtype=float),
            "low": np.asarray(low if low is not None else values - 0.5, dtype=float),
            "close": values,
            "volume": np.full(len(values), 100.0),
        }
    )


def test_resample_uses_completed_right_labeled_bars() -> None:
    exp0136 = load_exp0136()
    df = ohlcv([10.0, 11.0, 12.0, 13.0], high=[10.0, 12.0, 13.0, 14.0], low=[9.0, 8.0, 7.0, 6.0])

    out = exp0136.resample_ohlcv_completed(df, "15min")

    assert out.loc[0, "datetime"] == pd.Timestamp("2026-01-01 00:00:00")
    assert out.loc[0, "close"] == 10.0
    assert out.loc[1, "datetime"] == pd.Timestamp("2026-01-01 00:15:00")
    assert out.loc[1, "open"] == 11.0
    assert out.loc[1, "high"] == 14.0
    assert out.loc[1, "low"] == 6.0
    assert out.loc[1, "close"] == 13.0


def test_core_donchian_uses_shifted_channel_and_min_hold_before_flip() -> None:
    exp0136 = load_exp0136()
    df = ohlcv(
        [10.0, 11.0, 12.0, 13.5, 7.0, 6.0],
        high=[10.0, 11.0, 12.0, 13.5, 7.0, 6.0],
        low=[9.5, 10.5, 11.5, 13.0, 7.0, 6.0],
    )

    signals = exp0136.core_donchian_signals(df, entry_lookback=3, min_hold_bars=2)

    assert signals.tolist() == [1, 1, 1, 2, 2, 3]


def test_shifted_next_open_signals_preserve_first_bar_hold() -> None:
    exp0136 = load_exp0136()

    shifted = exp0136.shifted_next_open_signals(np.asarray([2, 2, 3, 1], dtype=int))

    assert shifted.tolist() == [1, 2, 2, 3]


def test_rolling_worst_and_overlap_ratio() -> None:
    exp0136 = load_exp0136()
    idx = pd.date_range("2026-01-01", periods=6, freq="1D")
    left = pd.Series([100.0, 90.0, 80.0, 85.0, 86.0, 87.0], index=idx)
    right = pd.Series([100.0, 101.0, 99.0, 80.0, 82.0, 83.0], index=idx)

    left_dd = exp0136.max_drawdown_period(left)
    right_dd = exp0136.max_drawdown_period(right)

    assert left_dd["start"] == pd.Timestamp("2026-01-01")
    assert left_dd["end"] == pd.Timestamp("2026-01-03")
    assert right_dd["start"] == pd.Timestamp("2026-01-02")
    assert right_dd["end"] == pd.Timestamp("2026-01-04")
    assert exp0136.overlap_ratio(left_dd, right_dd) == 0.5


def test_combo_daily_equity_uses_fixed_weight_daily_returns() -> None:
    exp0136 = load_exp0136()
    frame = pd.DataFrame(
        {
            "a": [100.0, 110.0, 121.0],
            "b": [100.0, 90.0, 99.0],
        },
        index=pd.date_range("2026-01-01", periods=3, freq="1D"),
    )

    combo = exp0136.combo_daily_equity(frame, {"a": 0.5, "b": 0.5})

    assert combo.iloc[0] == exp0136.INITIAL
    assert combo.iloc[1] == exp0136.INITIAL
    assert combo.iloc[2] == exp0136.INITIAL * 1.1


def test_top_overlap_counts_unique_overlapping_windows() -> None:
    exp0136 = load_exp0136()
    rows = [
        {
            "sleeve": "5m_v22_moirai_baseline",
            "entry_time": "2026-01-01",
            "exit_time": "2026-01-10",
            "month": "2026-01",
            "quarter": "2026Q1",
            "pnl": 10.0,
        },
        {
            "sleeve": "15m_core_donchian",
            "entry_time": "2026-01-05",
            "exit_time": "2026-01-12",
            "month": "2026-01",
            "quarter": "2026Q1",
            "pnl": 20.0,
        },
    ]

    overlap = exp0136.top_overlap_rows(rows)
    target = next(
        row
        for row in overlap
        if row["left"] == "5m_v22_moirai_baseline" and row["right"] == "15m_core_donchian"
    )

    assert target["top20_overlap_count_left"] == 1
    assert target["top20_overlap_count_right"] == 1
    assert target["top20_overlap_by_month"] == 1
    assert target["left_contribution_overlap"] == 1.0
    assert target["right_contribution_overlap"] == 1.0
