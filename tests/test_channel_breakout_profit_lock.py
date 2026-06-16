from __future__ import annotations

import numpy as np
import pandas as pd

from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy


def _ohlcv(close_values: list[float]) -> pd.DataFrame:
    close = np.asarray(close_values, dtype=float)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(close), freq="5min"),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        }
    )


def test_profit_lock_disabled_preserves_original_channel_breakout_signals() -> None:
    df = _ohlcv([10, 10, 10, 13, 14, 16, 15, 12, 9, 8, 11])
    params = {
        "entry_lookback": 3,
        "min_hold_bars": 0,
        "enable_long": True,
        "enable_short": True,
    }

    baseline = ChannelBreakoutTrendStrategy(**params).generate_signals(df, enable_short=True)
    disabled = ChannelBreakoutTrendStrategy(
        **params,
        profit_lock_enabled=False,
        profit_lock_activate_pct=0.01,
        profit_lock_giveback_ratio=0.01,
        profit_lock_atr_multiplier=1.0,
    ).generate_signals(df, enable_short=True)

    np.testing.assert_array_equal(disabled, baseline)


def test_profit_lock_closes_long_after_activated_profit_gives_back() -> None:
    df = _ohlcv([10, 10, 10, 13, 14, 16, 15, 12])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        enable_long=True,
        enable_short=True,
        profit_lock_enabled=True,
        profit_lock_activate_pct=0.10,
        profit_lock_giveback_ratio=0.25,
    )

    signals = strategy.generate_signals(df, enable_short=True)

    assert signals[3] == 2
    assert signals[5] == 2
    assert signals[6] == 0


def test_profit_lock_closes_short_after_activated_profit_gives_back() -> None:
    df = _ohlcv([20, 20, 20, 17, 16, 14, 15, 18])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        enable_long=True,
        enable_short=True,
        profit_lock_enabled=True,
        profit_lock_activate_pct=0.10,
        profit_lock_giveback_ratio=0.25,
    )

    signals = strategy.generate_signals(df, enable_short=True)

    assert signals[3] == 3
    assert signals[5] == 3
    assert signals[6] == 0

