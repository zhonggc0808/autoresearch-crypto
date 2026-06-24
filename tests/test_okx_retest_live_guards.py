from __future__ import annotations

import numpy as np
import pandas as pd

from dex.live.retest_guards import (
    guard_retest_hold_reentry,
    guard_retest_next_open,
    summarize_retest_gap,
)
from dex.live.retest_replay import replay_retest_bar_by_bar
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy


def _replay() -> pd.DataFrame:
    values = np.asarray([10, 10, 10, 10, 11, 10.1, 10.3, 10.4, 10.5], dtype=float)
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=len(values), freq="5min"),
            "open": values,
            "high": values,
            "low": values,
            "close": values,
            "volume": np.ones(len(values)),
        }
    )
    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=3,
        min_hold_bars=0,
        retest_enabled=True,
        retest_window_bars=3,
        retest_tolerance_pct=0.02,
        retest_entry_delay_bars=1,
    )
    return replay_retest_bar_by_bar(df, strategy)


def test_retest_next_open_guard_cancels_stale_entry() -> None:
    replay = _replay()
    messages: list[str] = []
    state = {"position": 0}

    signal = guard_retest_next_open(
        2,
        state,
        replay,
        pd.Timestamp("2024-01-01 00:40:00"),
        300,
        messages.append,
    )

    assert signal == 1
    assert state["scheduled_entry_missed_reason"] == "missed_next_open"
    assert "missed_next_open" in messages[-1]


def test_retest_gap_summary_reports_replayed_events() -> None:
    replay = _replay()
    summary = summarize_retest_gap(
        {"last_processed_bar": "2024-01-01 00:20:00"},
        replay,
        pd.Timestamp("2024-01-01 00:35:00"),
        300,
    )

    assert summary is not None
    assert summary["gap_bars"] == 2
    assert summary["events"] == {
        "retest_confirmed_entry_scheduled": 1,
        "scheduled_entry_executed": 1,
    }


def test_retest_hold_reentry_guard_blocks_flat_reentry() -> None:
    messages: list[str] = []
    state = {"position": 0}

    signal = guard_retest_hold_reentry(
        2,
        state,
        {"timestamp": "2024-01-01 00:00:00", "reason": "hold_long"},
        messages.append,
    )

    assert signal == 1
    assert state["retest_hold_reentry_blocked_reason"] == "hold_long"
    assert "suppress flat re-entry" in messages[-1]
