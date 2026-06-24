"""Retest pending-state replay helpers shared by offline and signal-only live checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class PendingBreakout:
    direction: int = 0
    created_i: int = -1
    level: float = 0.0


def replay_retest_bar_by_bar(df: pd.DataFrame, strategy: Any) -> pd.DataFrame:
    if strategy.breakout_atr_buffer > 0 or strategy.trend_ma_period > 0 or strategy.adx_threshold > 0:
        raise ValueError("replay_retest_bar_by_bar only supports the plain Donchian+BB retest setup")

    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    channel_high = (
        pd.Series(high).rolling(strategy.entry_lookback, min_periods=strategy.entry_lookback).max()
    ).shift(1)
    channel_low = (
        pd.Series(low).rolling(strategy.entry_lookback, min_periods=strategy.entry_lookback).min()
    ).shift(1)
    bb_upper = bb_lower = pd.Series(np.nan, index=df.index)
    if strategy.bollinger_breakout_enabled:
        mid = (
            pd.Series(close)
            .rolling(strategy.bollinger_window, min_periods=strategy.bollinger_window)
            .mean()
        )
        std = (
            pd.Series(close)
            .rolling(strategy.bollinger_window, min_periods=strategy.bollinger_window)
            .std()
        )
        bb_upper = (mid + strategy.bollinger_std_dev * std).shift(1)
        bb_lower = (mid - strategy.bollinger_std_dev * std).shift(1)

    pending = PendingBreakout()
    scheduled_dir = 0
    scheduled_i = -1
    position = 0
    entry_i = 0
    last_exit_i = -strategy.cooldown_bars
    rows: list[dict[str, Any]] = []

    for i in range(len(df)):
        pending_before = pending.direction
        pending_level_before = pending.level if pending.direction else np.nan
        pending_age_before = i - pending.created_i if pending.direction else np.nan
        executed_signal = 1
        reason = "warmup" if i < strategy.window else "hold"
        retest_hit = close_confirm = bb_still_valid = False
        pending_timed_out = False
        entry_scheduled_this_bar = False

        ch = float(channel_high.iloc[i]) if pd.notna(channel_high.iloc[i]) else np.nan
        cl = float(channel_low.iloc[i]) if pd.notna(channel_low.iloc[i]) else np.nan
        bu = float(bb_upper.iloc[i]) if pd.notna(bb_upper.iloc[i]) else np.nan
        bl = float(bb_lower.iloc[i]) if pd.notna(bb_lower.iloc[i]) else np.nan
        long_trigger = ch * (1.0 + strategy.breakout_buffer_pct) if np.isfinite(ch) else np.nan
        short_trigger = cl * (1.0 - strategy.breakout_buffer_pct) if np.isfinite(cl) else np.nan
        bb_long = (not strategy.bollinger_breakout_enabled) or (
            np.isfinite(bu) and close[i] > bu
        )
        bb_short = (not strategy.bollinger_breakout_enabled) or (
            np.isfinite(bl) and close[i] < bl
        )
        long_break = (
            strategy.enable_long
            and bb_long
            and np.isfinite(long_trigger)
            and close[i] > long_trigger
        )
        short_break = (
            strategy.enable_short
            and bb_short
            and np.isfinite(short_trigger)
            and close[i] < short_trigger
        )
        raw_signal = 2 if long_break else 3 if short_break else 1

        if i >= strategy.window:
            can_flip = i - entry_i >= strategy.min_hold_bars
            can_enter = i - last_exit_i >= strategy.cooldown_bars

            if scheduled_dir and position == 0 and i >= scheduled_i and can_enter:
                executed_signal = 2 if scheduled_dir > 0 else 3
                position = 1 if scheduled_dir > 0 else -1
                entry_i = i
                scheduled_dir = 0
                scheduled_i = -1
                pending = PendingBreakout()
                reason = "scheduled_entry_executed"
            elif pending.direction and position == 0 and not scheduled_dir:
                if i - pending.created_i > strategy.retest_window_bars:
                    pending = PendingBreakout()
                    pending_timed_out = True
                    reason = "pending_timeout"
                elif i > pending.created_i:
                    if pending.direction > 0:
                        retest_hit = low[i] <= pending.level * (
                            1.0 + strategy.retest_tolerance_pct
                        )
                        close_confirm = close[i] > pending.level * (
                            1.0 + getattr(strategy, "retest_min_reclaim_pct", 0.0)
                        )
                        bb_still_valid = (
                            not strategy.retest_require_bollinger_confirmation
                        ) or (
                            np.isfinite(bu)
                            and close[i]
                            > bu
                            * (
                                1.0
                                + getattr(strategy, "retest_min_bollinger_distance_pct", 0.0)
                            )
                        )
                    else:
                        retest_hit = high[i] >= pending.level * (
                            1.0 - strategy.retest_tolerance_pct
                        )
                        close_confirm = close[i] < pending.level * (
                            1.0 - getattr(strategy, "retest_min_reclaim_pct", 0.0)
                        )
                        bb_still_valid = (
                            not strategy.retest_require_bollinger_confirmation
                        ) or (
                            np.isfinite(bl)
                            and close[i]
                            < bl
                            * (
                                1.0
                                - getattr(strategy, "retest_min_bollinger_distance_pct", 0.0)
                            )
                        )
                    if retest_hit and close_confirm and bb_still_valid:
                        scheduled_dir = pending.direction
                        scheduled_i = i + strategy.retest_entry_delay_bars
                        pending = PendingBreakout()
                        entry_scheduled_this_bar = True
                        reason = "retest_confirmed_entry_scheduled"

            if reason in {"hold", "pending_timeout"}:
                if position == 1:
                    if can_flip and short_break:
                        executed_signal = 0
                        position = 0
                        pending = PendingBreakout(-1, i, short_trigger)
                        reason = "flip_short_pending_created"
                    else:
                        executed_signal = 2
                        reason = "hold_long"
                elif position == -1:
                    if can_flip and long_break:
                        executed_signal = 0
                        position = 0
                        pending = PendingBreakout(1, i, long_trigger)
                        reason = "flip_long_pending_created"
                    else:
                        executed_signal = 3
                        reason = "hold_short"
                elif can_enter and not scheduled_dir and not pending.direction:
                    if strategy.retest_enabled and long_break:
                        pending = PendingBreakout(1, i, long_trigger)
                        reason = "long_pending_created"
                    elif strategy.retest_enabled and short_break:
                        pending = PendingBreakout(-1, i, short_trigger)
                        reason = "short_pending_created"
                    elif long_break:
                        executed_signal = 2
                        position = 1
                        entry_i = i
                        reason = "long_entry"
                    elif short_break:
                        executed_signal = 3
                        position = -1
                        entry_i = i
                        reason = "short_entry"

        rows.append(
            {
                "bar_index": i,
                "timestamp": _bar_time(df, i),
                "open": open_[i],
                "high": high[i],
                "low": low[i],
                "close": close[i],
                "donchian_upper_shift1": ch,
                "donchian_lower_shift1": cl,
                "bb_upper_shift1": bu,
                "bb_lower_shift1": bl,
                "raw_breakout_signal": raw_signal,
                "pending_before": pending_before,
                "pending_after": pending.direction,
                "pending_direction": pending.direction,
                "pending_age": i - pending.created_i if pending.direction else np.nan,
                "pending_level": pending.level if pending.direction else np.nan,
                "pending_level_before": pending_level_before,
                "pending_age_before": pending_age_before,
                "retest_hit": retest_hit,
                "close_confirm": close_confirm,
                "bb_still_valid": bb_still_valid,
                "pending_timed_out": pending_timed_out,
                "entry_scheduled": entry_scheduled_this_bar or bool(scheduled_dir),
                "entry_due_bar": scheduled_i if scheduled_dir else np.nan,
                "executed_signal": executed_signal,
                "position_after": position,
                "reason": reason,
            }
        )

    return pd.DataFrame(rows)


def latest_retest_replay_row(df: pd.DataFrame, strategy: Any) -> dict[str, Any] | None:
    if not getattr(strategy, "retest_enabled", False):
        return None
    log = replay_retest_bar_by_bar(df, strategy)
    if log.empty:
        return None
    return log.iloc[-1].to_dict()


def format_retest_replay_row(row: dict[str, Any]) -> str:
    return (
        "[RetestReplay] "
        f"raw={int(row['raw_breakout_signal'])} "
        f"pending_before={int(row['pending_before'])} "
        f"pending_after={int(row['pending_after'])} "
        f"pending_level={_fmt(row['pending_level'])} "
        f"pending_age={_fmt(row['pending_age'], digits=0)} "
        f"retest_hit={bool(row['retest_hit'])} "
        f"close_confirm={bool(row['close_confirm'])} "
        f"bb_still_valid={bool(row['bb_still_valid'])} "
        f"entry_due_bar={_fmt(row['entry_due_bar'], digits=0)} "
        f"executed={int(row['executed_signal'])} "
        f"position_after={int(row['position_after'])} "
        f"reason={row['reason']}"
    )


def _bar_time(df: pd.DataFrame, i: int) -> Any:
    if "datetime" in df.columns:
        return df["datetime"].iloc[i]
    if "timestamp" in df.columns:
        return df["timestamp"].iloc[i]
    return i


def _fmt(value: Any, digits: int = 2) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"
