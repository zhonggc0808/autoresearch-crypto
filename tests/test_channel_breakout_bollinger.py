from __future__ import annotations

import numpy as np
import pandas as pd

from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy


def _df(close: list[float]) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": values,
            "low": values,
            "close": values,
            "volume": np.ones(len(values)),
        }
    )


def test_bollinger_confirmation_is_disabled_by_default() -> None:
    base = ChannelBreakoutTrendStrategy(entry_lookback=5, min_hold_bars=0)

    assert base.bollinger_breakout_enabled is False
    assert base.bollinger_window == 20
    assert base.bollinger_std_dev == 2.0
    assert base.window == 5


def test_bollinger_confirmation_extends_warmup_when_enabled() -> None:
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=5,
        min_hold_bars=0,
        bollinger_breakout_enabled=True,
        bollinger_window=12,
        bollinger_std_dev=1.5,
    )

    assert strategy.window == 12
    assert strategy.warmup_bars == 12


def test_bollinger_confirmation_uses_completed_previous_band() -> None:
    df = _df([10, 10, 20, 10, 10, 10, 10, 13, 13.1, 25])
    no_bb = ChannelBreakoutTrendStrategy(entry_lookback=3, min_hold_bars=0)
    with_bb = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        bollinger_breakout_enabled=True,
        bollinger_window=8,
        bollinger_std_dev=1.5,
    )

    no_bb_signals = no_bb.generate_signals(df)
    bb_signals = with_bb.generate_signals(df)

    assert no_bb_signals[8] == 2
    assert bb_signals[8] == 1
    assert bb_signals[9] == 2
