from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0135 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.py"


def load_exp0135():
    spec = importlib.util.spec_from_file_location("exp0135_donchian_internal_return", EXP0135)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0135_donchian_internal_return"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(close: list[float], high: list[float] | None = None, low: list[float] | None = None) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": np.asarray(high if high is not None else [x + 1.0 for x in close], dtype=float),
            "low": np.asarray(low if low is not None else [x - 1.0 for x in close], dtype=float),
            "close": values,
            "volume": np.full(len(values), 1000.0),
        }
    )


def feature_frame(n: int, *, upper: float = 100.0, lower: float = 90.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=n, freq="5min"),
            "donchian_upper_prev": np.full(n, upper),
            "donchian_lower_prev": np.full(n, lower),
            "bb_upper_prev": np.full(n, 110.0),
            "bb_lower_prev": np.full(n, 80.0),
        }
    )


def trade(entry: int = 1, exit_: int = 9, pnl: float = 10.0) -> dict[str, float | int | str]:
    return {
        "entry_step": entry,
        "step": exit_,
        "pnl": pnl,
        "entry_price": 105.0,
        "entry_notional": 1000.0,
        "type": "sell",
    }


def test_channel_features_use_prior_completed_donchian_and_bb_values() -> None:
    exp0135 = load_exp0135()
    df = ohlcv(
        [10.0, 11.0, 12.0, 100.0],
        high=[10.0, 11.0, 12.0, 1000.0],
        low=[9.0, 8.0, 7.0, 1.0],
    )

    features = exp0135.build_channel_features(df, donchian_window=3, bb_window=3, bb_std_dev=1.5)

    assert features.loc[3, "donchian_upper_prev"] == 12.0
    assert features.loc[3, "donchian_lower_prev"] == 7.0
    prior = pd.Series([10.0, 11.0, 12.0])
    expected_upper = prior.mean() + 1.5 * prior.std()
    assert features.loc[3, "bb_upper_prev"] == expected_upper


def test_internal_return_signal_is_directional_and_tolerance_aware() -> None:
    exp0135 = load_exp0135()

    assert exp0135.internal_return_signal(1, 100.0, 100.0, 90.0, 0.0)
    assert not exp0135.internal_return_signal(1, 99.6, 100.0, 90.0, 0.005)
    assert exp0135.internal_return_signal(1, 99.5, 100.0, 90.0, 0.005)
    assert exp0135.internal_return_signal(-1, 90.0, 100.0, 90.0, 0.0)
    assert not exp0135.internal_return_signal(-1, 90.4, 100.0, 90.0, 0.005)
    assert exp0135.internal_return_signal(-1, 90.45, 100.0, 90.0, 0.005)


def test_also_inside_bb_requires_previous_envelope_containment() -> None:
    exp0135 = load_exp0135()

    assert exp0135.also_inside_bb(100.0, 110.0, 90.0)
    assert not exp0135.also_inside_bb(111.0, 110.0, 90.0)
    assert not exp0135.also_inside_bb(89.0, 110.0, 90.0)


def test_same_bar_baseline_reversal_is_not_counted() -> None:
    exp0135 = load_exp0135()
    df = ohlcv([105.0, 106.0, 99.0, 98.0])
    signals = np.asarray([2, 2, 3, 3], dtype=int)
    features = feature_frame(len(df))
    regimes = np.asarray(["BULL"] * len(df), dtype=object)

    events, diagnostics = exp0135.collect_internal_return_events(
        signals,
        df,
        features,
        regimes,
        [trade(entry=1, exit_=3)],
        {1: {"realized_return": 0.01, "life_mfe": 0.05}},
        top20_cutoff=5.0,
        worst20_entries=set(),
    )

    assert events == []
    assert diagnostics["baseline_conflict_skipped"] == 1


def test_collect_events_records_rising_edge_and_post_signal_path() -> None:
    exp0135 = load_exp0135()
    df = ohlcv([105.0, 106.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0])
    signals = np.asarray([2] * len(df), dtype=int)
    features = feature_frame(len(df))
    regimes = np.asarray(["BEAR"] * len(df), dtype=object)

    events, diagnostics = exp0135.collect_internal_return_events(
        signals,
        df,
        features,
        regimes,
        [trade(entry=1, exit_=9, pnl=10.0)],
        {1: {"realized_return": 0.02, "life_mfe": 0.10}},
        top20_cutoff=5.0,
        worst20_entries={2},
    )

    zero_tol_events = [event for event in events if event["tol_bp"] == 0]
    assert len(zero_tol_events) == 1
    event = zero_tol_events[0]
    assert event["bar"] == 2
    assert event["regime"] == "BEAR"
    assert event["direction"] == "long"
    assert event["also_inside_bb"] is True
    assert event["is_top20"] is True
    assert event["lead_time_bars"] == 7
    assert event["attribution_only"] is True
    assert event["post_return_6"] == 93.0 / 99.0 - 1.0
    assert diagnostics["baseline_conflict_skipped"] == 0


def test_bucket_aggregation_uses_required_group_dimensions() -> None:
    exp0135 = load_exp0135()
    events = [
        {
            "regime": "BEAR",
            "direction": "short",
            "tol_bp": 50,
            "also_inside_bb": True,
            "is_top20": False,
            "is_worst20": True,
            "entry_bar": 10,
            "lead_time_bars": 20,
            "bars_held_at_signal": 5,
            "signal_return_from_entry": 0.04,
            "baseline_realized_return": -0.01,
            "signal_vs_baseline_return_delta": 0.05,
            "giveback_capture_ratio": 0.6,
            "signal_mfe_capture_ratio": 0.4,
            "base_trade_pnl": -100.0,
            **{f"post_return_{h}": -0.01 for h in exp0135.POST_SIGNAL_HORIZONS},
        }
    ]

    buckets = exp0135.aggregate_buckets(events)

    assert buckets[0]["regime"] == "BEAR"
    assert buckets[0]["direction"] == "short"
    assert buckets[0]["tol_bp"] == 50
    assert buckets[0]["also_inside_bb"] is True
    assert buckets[0]["is_top20"] is False
    assert buckets[0]["events"] == 1
    assert buckets[0]["adverse_rate_72"] == 1.0
