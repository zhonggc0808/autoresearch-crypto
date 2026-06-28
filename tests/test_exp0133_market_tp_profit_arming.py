from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0133 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.py"


def load_exp0133():
    spec = importlib.util.spec_from_file_location("exp0133_market_tp_profit_arming", EXP0133)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0133_market_tp_profit_arming"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(close: list[float], atr: float = 5.0) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": values + 1.0,
            "low": values - 1.0,
            "close": values,
            "volume": np.full(len(values), 1000.0),
            "atr": np.full(len(values), atr),
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


def wick_spec(exp0133, threshold: float):
    market = exp0133.exp0131.VariantSpec(
        variant="test_2h_wick",
        use_2h_wick=True,
        wick_shadow_ratio=0.75,
        wick_volume_ratio=1.5,
    )
    return exp0133.ArmedVariantSpec(
        variant=f"test_2h_wick_armATR{threshold}",
        signal_variant=market.variant,
        market_spec=market,
        arming_threshold_atr=threshold,
    )


def base_trades() -> list[dict[str, float]]:
    return [{"entry_step": 1, "step": 5, "pnl": 10.0, "entry_notional": 100.0}]


def test_profit_arming_latches_and_allows_later_exit_after_profit_recedes() -> None:
    exp0133 = load_exp0133()
    df = ohlcv([100.0, 100.0, 90.0, 96.0, 96.0])
    signals = np.array([3, 3, 3, 3, 3])
    features = empty_features(len(df))
    features.loc[3, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]

    out, exits, arms = exp0133.apply_profit_armed_market_tp(
        signals,
        df,
        features,
        wick_spec(exp0133, 1.5),
        base_trades(),
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert out.tolist() == [3, 3, 3, 0, 1]
    assert len(arms) == 1
    assert arms[0]["bar"] == 2
    assert len(exits) == 1
    assert exits[0]["armed_bar"] == 2
    assert exits[0]["open_profit_atr"] < exits[0]["armed_open_profit_atr"]
    assert exits[0]["current_return_after_cost"] > 0


def test_profit_arming_blocks_market_signal_before_threshold() -> None:
    exp0133 = load_exp0133()
    df = ohlcv([100.0, 100.0, 96.0, 95.0, 94.0])
    signals = np.array([3, 3, 3, 3, 3])
    features = empty_features(len(df))
    features.loc[2, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]

    out, exits, arms = exp0133.apply_profit_armed_market_tp(
        signals,
        df,
        features,
        wick_spec(exp0133, 3.0),
        base_trades(),
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert out.tolist() == signals.tolist()
    assert exits == []
    assert arms == []


def test_profit_arming_does_not_relabel_same_bar_baseline_reversal() -> None:
    exp0133 = load_exp0133()
    df = ohlcv([100.0, 100.0, 90.0, 91.0])
    signals = np.array([3, 3, 2, 2])
    features = empty_features(len(df))
    features.loc[2, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]

    out, exits, arms = exp0133.apply_profit_armed_market_tp(
        signals,
        df,
        features,
        wick_spec(exp0133, 1.5),
        [{"entry_step": 1, "step": 3, "pnl": 10.0, "entry_notional": 100.0}],
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert out.tolist() == signals.tolist()
    assert arms[0]["bar"] == 2
    assert exits == []


def test_sequence_setup_before_arming_is_ignored() -> None:
    exp0133 = load_exp0133()
    df = ohlcv([100.0, 100.0, 96.0, 90.0, 91.0])
    signals = np.array([3, 3, 3, 3, 3])
    features = empty_features(len(df))
    features.loc[2, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]
    features.loc[3, "macd_bull_cross_2h"] = True
    market = exp0133.exp0131.VariantSpec(
        variant="test_sequence",
        use_2h_wick=True,
        wick_shadow_ratio=0.75,
        wick_volume_ratio=1.5,
        sequence_macd_tf="2h",
        sequence_window_bars=3,
    )
    spec = exp0133.ArmedVariantSpec(
        variant="test_sequence_armATR1p5",
        signal_variant=market.variant,
        market_spec=market,
        arming_threshold_atr=1.5,
    )

    out, exits, arms = exp0133.apply_profit_armed_market_tp(
        signals,
        df,
        features,
        spec,
        base_trades(),
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert out.tolist() == signals.tolist()
    assert arms[0]["bar"] == 3
    assert exits == []


def test_build_armed_matrix_has_three_signals_times_four_thresholds() -> None:
    exp0133 = load_exp0133()

    variants = exp0133.build_armed_matrix()

    assert len(variants) == 12
    assert {v.signal_variant for v in variants} == {
        "1h_strict_engulf_v2p0_macd4h",
        "2h_wick_r75_v2p0_then_macd2h_12h",
        "2h_wick_r75_v1p5_macd2h",
    }
    assert {v.arming_threshold_atr for v in variants} == {1.5, 2.0, 2.5, 3.0}
