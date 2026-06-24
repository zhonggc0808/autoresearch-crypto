from __future__ import annotations

import numpy as np
import pandas as pd

from dex.live.retest_replay import format_retest_replay_row, replay_retest_bar_by_bar
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


def test_bar_by_bar_replay_matches_strategy_retest_entry_timing() -> None:
    df = _df([10, 10, 10, 10, 11, 10.1, 10.3, 10.4])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        retest_enabled=True,
        retest_window_bars=3,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
    )

    replay = replay_retest_bar_by_bar(df, strategy)
    signals = strategy.generate_signals(df)

    assert replay["executed_signal"].to_numpy().tolist() == signals.tolist()
    assert replay.loc[4, "reason"] == "long_pending_created"
    assert replay.loc[4, "executed_signal"] == 1
    assert replay.loc[5, "reason"] == "retest_confirmed_entry_scheduled"
    assert replay.loc[5, "entry_due_bar"] == 6
    assert replay.loc[6, "reason"] == "scheduled_entry_executed"
    assert replay.loc[6, "executed_signal"] == 2
    assert "reason=scheduled_entry_executed" in format_retest_replay_row(
        replay.loc[6].to_dict()
    )


def test_bar_by_bar_replay_times_out_pending_without_entry() -> None:
    df = _df([10, 10, 10, 10, 11, 11.5, 11.7, 11.9, 12.0])
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        retest_enabled=True,
        retest_window_bars=2,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
    )

    replay = replay_retest_bar_by_bar(df, strategy)

    assert replay["pending_timed_out"].any()
    assert not np.any(replay["executed_signal"].to_numpy() == 2)
