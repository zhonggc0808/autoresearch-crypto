from __future__ import annotations

import numpy as np
import pandas as pd

import scripts.run_kraken_breakout_overlay_experiments as kraken
from scripts.run_kraken_breakout_overlay_experiments import (
    build_kraken_position_sizes,
    mc_size_multiplier,
)


def test_mc_size_multiplier_buckets() -> None:
    assert mc_size_multiplier(0.54) == 0.0
    assert mc_size_multiplier(0.55) == 0.5
    assert mc_size_multiplier(0.65) == 1.0
    assert mc_size_multiplier(0.75) == 1.5


def test_quality_size_multiplier_buckets() -> None:
    assert kraken.quality_size_multiplier(0.39) == 0.0
    assert kraken.quality_size_multiplier(0.40) == 0.5
    assert kraken.quality_size_multiplier(0.55) == 1.0
    assert kraken.quality_size_multiplier(0.70) == 1.5


def test_filter_only_changes_new_entry_bars() -> None:
    df = pd.DataFrame(
        {
            "close": [100.0, 99.0, 98.0, 99.0, 99.0, 99.0],
            "volume": [1.0, 1.0, 1.0, 10.0, 1.0, 1.0],
        }
    )
    signals = np.array([1, 1, 1, 2, 1, 0])

    sizes, stats = build_kraken_position_sizes(
        df,
        signals,
        0.45,
        "kraken_long_filter_size",
        rsi_period=2,
        vol_period=2,
        vol_min_pct=0.0,
        vol_max_pct=10.0,
        mc_min_conf=0.0,
        mc_paths=1,
        mc_horizon_bars=1,
        mc_return_lookback=2,
    )

    assert sizes[3] > 0.0
    assert sizes[4] == 0.45
    assert stats["long_entry_count"] == 1


def test_long_only_overlay_leaves_short_entries_at_base_size() -> None:
    df = pd.DataFrame({"close": [100.0, 99.0], "volume": [0.0, 0.0]})
    signals = np.array([3, 0])

    sizes, stats = build_kraken_position_sizes(
        df,
        signals,
        0.45,
        "kraken_long_filter_size",
        rsi_period=1,
        vol_period=1,
        mc_paths=1,
        mc_horizon_bars=1,
        mc_return_lookback=1,
    )

    assert sizes[0] == 0.45
    assert stats["short_entry_count"] == 1
    assert stats["unchanged_entry_count"] == 1


def test_symmetric_overlay_applies_short_mirror_filter() -> None:
    df = pd.DataFrame({"close": [100.0, 99.0], "volume": [0.0, 0.0]})
    signals = np.array([3, 0])

    sizes, stats = build_kraken_position_sizes(
        df,
        signals,
        0.45,
        "kraken_symmetric_filter_size",
        rsi_period=1,
        vol_period=1,
        mc_paths=1,
        mc_horizon_bars=1,
        mc_return_lookback=1,
    )

    assert sizes[0] == 0.0
    assert stats["short_entry_count"] == 1
    assert stats["blocked_entry_count"] == 1


def test_closed_bar_quality_uses_previous_closed_bar(monkeypatch) -> None:
    monkeypatch.setattr(kraken, "ATR_PERCENTILE_LOOKBACK", 2)
    monkeypatch.setattr(kraken, "VOLUME_Z_LOOKBACK", 2)
    df = pd.DataFrame(
        {
            "close": [100.0, 100.0, 100.0, 100.0, 120.0],
            "high": [100.0, 100.0, 100.0, 100.0, 120.0],
            "low": [99.0, 99.0, 99.0, 99.0, 119.0],
            "volume": [10.0, 10.0, 10.0, 10.0, 100.0],
        },
        index=pd.date_range("2026-01-01", periods=5, freq="5min"),
    )
    signals = np.array([1, 1, 1, 1, 2])

    sizes, stats = build_kraken_position_sizes(
        df,
        signals,
        0.45,
        "closed_bar_quality_sizing",
        mc_return_lookback=2,
    )

    assert sizes[4] == 0.0
    assert stats["blocked_entry_count"] == 1
