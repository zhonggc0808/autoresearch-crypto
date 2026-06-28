from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0134 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.py"


def load_exp0134():
    spec = importlib.util.spec_from_file_location("exp0134_riskoff_trailing_stop", EXP0134)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0134_riskoff_trailing_stop"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(
    close: list[float],
    *,
    high: list[float] | None = None,
    low: list[float] | None = None,
    atr: list[float] | None = None,
) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": np.asarray(high if high is not None else [x + 1.0 for x in close], dtype=float),
            "low": np.asarray(low if low is not None else [x - 1.0 for x in close], dtype=float),
            "close": values,
            "volume": np.full(len(values), 1000.0),
            "atr": np.asarray(atr if atr is not None else [5.0] * len(values), dtype=float),
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


def add_short_riskoff(features: pd.DataFrame, bar: int) -> None:
    features.loc[bar, ["lower_shadow_ratio_2h", "close_pos_2h", "volume_ratio_2h"]] = [0.9, 0.8, 2.0]
    features.loc[bar, "macd_bull_cross_2h"] = True


def base_trades() -> list[dict[str, float]]:
    return [{"entry_step": 1, "step": 8, "pnl": 10.0, "entry_notional": 100.0}]


def spec(exp0134, atr_multiplier: float = 1.0, ttl_mode: str = "until_baseline_exit"):
    return exp0134.RiskoffTrailingSpec(
        variant=f"test_trail_{atr_multiplier}_{ttl_mode}",
        atr_multiplier=atr_multiplier,
        ttl_mode=ttl_mode,
    )


def run_overlay(exp0134, signals, df, features, variant):
    return exp0134.apply_riskoff_trailing_stop(
        np.asarray(signals, dtype=int),
        df,
        features,
        variant,
        base_trades(),
        top20_cutoff=5.0,
        worst20_entries=set(),
    )


def test_activation_does_not_exit_even_if_same_bar_close_crosses_new_stop() -> None:
    exp0134 = load_exp0134()
    df = ohlcv(
        [100.0, 100.0, 100.0, 96.0, 96.0],
        high=[101.0, 101.0, 110.0, 100.0, 100.0],
        low=[99.0, 99.0, 90.0, 90.0, 90.0],
        atr=[5.0] * 5,
    )
    signals = [3, 3, 3, 3, 3]
    features = empty_features(len(df))
    add_short_riskoff(features, 2)

    out, events = run_overlay(exp0134, signals, df, features, spec(exp0134))

    assert out.tolist() == [3, 3, 3, 0, 1]
    assert [event["event"] for event in events] == ["riskoff_activate", "riskoff_stop"]
    assert events[0]["bar"] == 2
    assert events[1]["bar"] == 3


def test_stop_uses_completed_close_not_intrabar_high() -> None:
    exp0134 = load_exp0134()
    df = ohlcv(
        [100.0, 100.0, 90.0, 93.0, 94.5, 94.5],
        high=[101.0, 101.0, 91.0, 110.0, 100.0, 100.0],
        low=[99.0, 99.0, 90.0, 89.0, 89.0, 89.0],
        atr=[5.0] * 6,
    )
    signals = [3, 3, 3, 3, 3, 3]
    features = empty_features(len(df))
    add_short_riskoff(features, 2)

    out, events = run_overlay(exp0134, signals, df, features, spec(exp0134))

    assert out.tolist() == [3, 3, 3, 3, 0, 1]
    assert [event["event"] for event in events] == ["riskoff_activate", "riskoff_stop"]
    assert events[1]["bar"] == 4


def test_atr_ref_is_fixed_at_activation() -> None:
    exp0134 = load_exp0134()
    df = ohlcv(
        [100.0, 100.0, 90.0, 95.0, 95.0],
        high=[101.0, 101.0, 91.0, 100.0, 100.0],
        low=[99.0, 99.0, 90.0, 90.0, 90.0],
        atr=[5.0, 5.0, 10.0, 1.0, 1.0],
    )
    signals = [3, 3, 3, 3, 3]
    features = empty_features(len(df))
    add_short_riskoff(features, 2)

    out, events = run_overlay(exp0134, signals, df, features, spec(exp0134))

    assert out.tolist() == signals
    assert [event["event"] for event in events] == ["riskoff_activate"]
    assert events[0]["atr_ref"] == 10.0
    assert events[0]["stop_price"] == 100.0


def test_ttl_expiry_cancels_trailing_without_forced_exit(monkeypatch) -> None:
    exp0134 = load_exp0134()
    monkeypatch.setattr(exp0134, "TTL_24H_BARS", 1)
    df = ohlcv(
        [100.0, 100.0, 90.0, 91.0, 92.0, 92.0],
        high=[101.0, 101.0, 91.0, 92.0, 93.0, 93.0],
        low=[99.0, 99.0, 90.0, 90.0, 90.0, 90.0],
        atr=[5.0] * 6,
    )
    signals = [3, 3, 3, 3, 3, 3]
    features = empty_features(len(df))
    add_short_riskoff(features, 2)

    out, events = run_overlay(exp0134, signals, df, features, spec(exp0134, ttl_mode="24h"))

    assert out.tolist() == signals
    assert [event["event"] for event in events] == ["riskoff_activate", "riskoff_cancel_ttl"]
    assert events[1]["bar"] == 4


def test_baseline_reversal_has_priority_over_same_bar_riskoff_activation() -> None:
    exp0134 = load_exp0134()
    df = ohlcv([100.0, 100.0, 90.0, 91.0])
    signals = [3, 3, 2, 2]
    features = empty_features(len(df))
    add_short_riskoff(features, 2)

    out, events = run_overlay(exp0134, signals, df, features, spec(exp0134))

    assert out.tolist() == signals
    assert events == []


def test_build_matrix_has_two_atr_multipliers_times_two_ttl_modes() -> None:
    exp0134 = load_exp0134()

    variants = exp0134.build_matrix()

    assert len(variants) == 4
    assert {v.atr_multiplier for v in variants} == {1.0, 1.5}
    assert {v.ttl_mode for v in variants} == {"until_baseline_exit", "24h"}
