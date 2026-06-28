from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0131 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py"


def load_exp0131():
    spec = importlib.util.spec_from_file_location("exp0131_market_signal_tp", EXP0131)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0131_market_signal_tp"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(close: list[float]) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": values + 1.0,
            "low": values - 1.0,
            "close": values,
            "volume": np.full(len(values), 1000.0),
        }
    )


def empty_features(n: int) -> pd.DataFrame:
    features = pd.DataFrame({"datetime": pd.date_range("2026-01-01", periods=n, freq="5min")})
    for col in [
        "lower_shadow_ratio_2h",
        "upper_shadow_ratio_2h",
        "close_pos_2h",
        "volume_ratio_2h",
        "volume_ratio_1h",
        "oi_change_2h",
        "oi_change_4h",
        "macd_hist_2h",
        "macd_hist_4h",
    ]:
        features[col] = np.nan
    for col in [
        "bullish_engulf_strict_1h",
        "bearish_engulf_strict_1h",
        "bullish_engulf_loose_1h",
        "bearish_engulf_loose_1h",
        "macd_bull_cross_2h",
        "macd_bear_cross_2h",
        "macd_bull_cross_4h",
        "macd_bear_cross_4h",
        "daily_bull_div_context",
        "daily_bear_div_context",
    ]:
        features[col] = False
    return features


def test_oi_features_are_shifted_before_alignment() -> None:
    exp0131 = load_exp0131()
    oi = pd.DataFrame(
        {
            "available_at": pd.date_range("2026-01-01", periods=4, freq="1h"),
            "bybit_open_interest": [100.0, 95.0, 80.0, 70.0],
        }
    )

    features = exp0131.build_oi_feature_frame(oi).set_index("available_at")

    assert np.isnan(features.loc[pd.Timestamp("2026-01-01 02:00"), "oi_change_2h"])
    assert features.loc[pd.Timestamp("2026-01-01 03:00"), "oi_change_2h"] == pytest.approx(-0.20)


def test_asof_alignment_does_not_use_future_completed_htf_bar() -> None:
    exp0131 = load_exp0131()
    df = ohlcv([100.0] * 26)
    frame = pd.DataFrame(
        {
            "available_at": [pd.Timestamp("2026-01-01 02:00")],
            "lower_shadow_ratio_2h": [0.9],
        }
    )

    aligned = exp0131.align_feature_frames(df, [frame])

    before = df.index[df["datetime"] == pd.Timestamp("2026-01-01 01:55")][0]
    at_close = df.index[df["datetime"] == pd.Timestamp("2026-01-01 02:00")][0]
    assert np.isnan(aligned.loc[before, "lower_shadow_ratio_2h"])
    assert aligned.loc[at_close, "lower_shadow_ratio_2h"] == 0.9


def test_market_tp_exits_profitable_short_and_locks_same_direction() -> None:
    exp0131 = load_exp0131()
    df = ohlcv([100.0, 100.0, 90.0, 91.0, 92.0, 93.0])
    signals = np.array([3, 3, 3, 3, 2, 2])
    features = empty_features(len(df))
    features.loc[2, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]
    variant = exp0131.VariantSpec(
        variant="test_2h_wick",
        use_2h_wick=True,
        wick_shadow_ratio=0.75,
        wick_volume_ratio=1.5,
    )

    out, exits = exp0131.apply_market_signal_tp(
        signals,
        df,
        features,
        variant,
        [{"entry_step": 1, "step": 5, "pnl": 10.0}],
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert out.tolist() == [3, 3, 0, 1, 2, 2]
    assert len(exits) == 1
    assert exits[0]["reasons"] == "2h_lower_wick"


def test_market_tp_does_not_relabel_same_bar_baseline_reversal() -> None:
    exp0131 = load_exp0131()
    df = ohlcv([100.0, 100.0, 90.0, 91.0])
    signals = np.array([3, 3, 2, 2])
    features = empty_features(len(df))
    features.loc[2, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]
    variant = exp0131.VariantSpec(
        variant="test_2h_wick",
        use_2h_wick=True,
        wick_shadow_ratio=0.75,
        wick_volume_ratio=1.5,
    )

    out, exits = exp0131.apply_market_signal_tp(
        signals,
        df,
        features,
        variant,
        [{"entry_step": 1, "step": 3, "pnl": 10.0}],
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert out.tolist() == signals.tolist()
    assert exits == []


def test_sequence_wick_then_macd_waits_for_confirmation() -> None:
    exp0131 = load_exp0131()
    df = ohlcv([100.0, 100.0, 90.0, 91.0, 92.0, 93.0])
    signals = np.array([3, 3, 3, 3, 3, 3])
    features = empty_features(len(df))
    features.loc[2, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]
    features.loc[4, "macd_bull_cross_2h"] = True
    variant = exp0131.VariantSpec(
        variant="test_sequence",
        use_2h_wick=True,
        wick_shadow_ratio=0.75,
        wick_volume_ratio=1.5,
        sequence_macd_tf="2h",
        sequence_window_bars=3,
    )

    out, exits = exp0131.apply_market_signal_tp(
        signals,
        df,
        features,
        variant,
        [{"entry_step": 1, "step": 5, "pnl": 10.0}],
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert out.tolist() == [3, 3, 3, 3, 0, 1]
    assert len(exits) == 1
    assert "2h_lower_wick_setup@" in exits[0]["reasons"]
    assert "macd_bull_cross_2h" in exits[0]["reasons"]
