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


def test_retest_is_disabled_by_default() -> None:
    strategy = ChannelBreakoutTrendStrategy(entry_lookback=3, min_hold_bars=0)

    assert strategy.retest_enabled is False
    assert strategy.retest_window_bars == 0
    assert strategy.retest_tolerance_pct == 0.0
    assert strategy.retest_entry_delay_bars == 1
    assert strategy.retest_require_bollinger_confirmation is False


def test_retest_requires_valid_parameters() -> None:
    with pytest.raises(ValueError, match="retest_window_bars"):
        ChannelBreakoutTrendStrategy(entry_lookback=3, retest_enabled=True)

    with pytest.raises(ValueError, match="retest_tolerance_pct"):
        ChannelBreakoutTrendStrategy(
            entry_lookback=3,
            retest_enabled=True,
            retest_window_bars=3,
            retest_tolerance_pct=-0.1,
        )

    with pytest.raises(ValueError, match="retest_entry_delay_bars"):
        ChannelBreakoutTrendStrategy(
            entry_lookback=3,
            retest_enabled=True,
            retest_window_bars=3,
            retest_entry_delay_bars=0,
        )

    with pytest.raises(ValueError, match="bollinger_breakout_enabled"):
        ChannelBreakoutTrendStrategy(
            entry_lookback=3,
            retest_enabled=True,
            retest_window_bars=3,
            retest_require_bollinger_confirmation=True,
        )


def test_retest_delays_breakout_entry_until_after_completed_confirmation_bar() -> None:
    df = _df([10, 10, 10, 10, 11, 10.1, 10.3, 10.4])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        retest_enabled=True,
        retest_window_bars=3,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
    )

    signals = strategy.generate_signals(df)

    assert signals[4] == 1
    assert signals[5] == 1
    assert signals[6] == 2


def test_retest_times_out_without_entry_when_pullback_does_not_arrive() -> None:
    df = _df([10, 10, 10, 10, 11, 11.5, 11.7, 11.9, 12.0])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        retest_enabled=True,
        retest_window_bars=2,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
    )

    signals = strategy.generate_signals(df)

    assert not np.any(signals == 2)


def test_retest_bb_confirmation_can_block_a_retest_that_loses_bb_direction() -> None:
    df = _df([10, 10, 10, 10, 11, 10.1, 10.3, 10.4])
    strategy = ChannelBreakoutTrendStrategy(
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
    )

    signals = strategy.generate_signals(df)

    assert not np.any(signals == 2)
