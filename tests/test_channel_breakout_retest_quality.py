from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy


def _df(
    close: list[float],
    high: list[float] | None = None,
    low: list[float] | None = None,
) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": np.asarray(high if high is not None else close, dtype=float),
            "low": np.asarray(low if low is not None else close, dtype=float),
            "close": values,
            "volume": np.ones(len(values)),
        }
    )


def test_retest_quality_params_are_disabled_by_default() -> None:
    strategy = ChannelBreakoutTrendStrategy(entry_lookback=3, min_hold_bars=0)

    assert strategy.retest_min_reclaim_pct == 0.0
    assert strategy.retest_min_bollinger_distance_pct == 0.0


def test_retest_quality_rejects_negative_thresholds() -> None:
    with pytest.raises(ValueError, match="retest_min_reclaim_pct"):
        ChannelBreakoutTrendStrategy(
            entry_lookback=3,
            retest_enabled=True,
            retest_window_bars=3,
            retest_min_reclaim_pct=-0.001,
        )

    with pytest.raises(ValueError, match="retest_min_bollinger_distance_pct"):
        ChannelBreakoutTrendStrategy(
            entry_lookback=3,
            retest_enabled=True,
            retest_window_bars=3,
            retest_min_bollinger_distance_pct=-0.001,
        )


def test_retest_min_reclaim_blocks_weak_long_reclaim() -> None:
    df = _df([10, 10, 10, 10, 11, 10.1, 10.4, 10.6])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        retest_enabled=True,
        retest_window_bars=3,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
        retest_min_reclaim_pct=0.02,
    )

    signals = strategy.generate_signals(df)

    assert not np.any(signals == 2)


def test_retest_min_reclaim_allows_strong_long_reclaim() -> None:
    df = _df([10, 10, 10, 10, 11, 10.25, 10.4, 10.6])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        retest_enabled=True,
        retest_window_bars=3,
        retest_tolerance_pct=0.03,
        retest_entry_delay_bars=1,
        retest_min_reclaim_pct=0.02,
    )

    signals = strategy.generate_signals(df)

    assert signals[6] == 2


def test_retest_min_bollinger_distance_blocks_weak_bb_long_confirmation() -> None:
    df = _df(
        [10, 10, 10, 10, 11, 10.4, 10.5, 10.6],
        low=[10, 10, 10, 10, 11, 10.1, 10.5, 10.6],
    )
    base = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        bollinger_breakout_enabled=True,
        bollinger_window=3,
        bollinger_std_dev=0.1,
        retest_enabled=True,
        retest_window_bars=3,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
        retest_require_bollinger_confirmation=True,
        retest_min_bollinger_distance_pct=0.0,
    )
    strict = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        bollinger_breakout_enabled=True,
        bollinger_window=3,
        bollinger_std_dev=0.1,
        retest_enabled=True,
        retest_window_bars=3,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
        retest_require_bollinger_confirmation=True,
        retest_min_bollinger_distance_pct=0.02,
    )

    base_signals = base.generate_signals(df)
    strict_signals = strict.generate_signals(df)

    assert base_signals[6] == 2
    assert not np.any(strict_signals == 2)
