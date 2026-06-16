"""Exit overlays applied after regime routing for research candidates."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

BARS_PER_DAY_5M = 288


def build_previous_completed_daily_ema(df: pd.DataFrame, period: int) -> np.ndarray:
    """Return the EMA of completed daily closes aligned to each intraday bar."""
    timestamps = _timestamps(df)
    tmp = pd.DataFrame(
        {
            "date": timestamps.dt.floor("D"),
            "close": df["close"].to_numpy(dtype=float),
        }
    )
    daily_close = tmp.groupby("date")["close"].last().sort_index()
    completed_ema = daily_close.ewm(span=period, adjust=False).mean().shift(1)
    return tmp["date"].map(completed_ema).to_numpy(dtype=float)


def apply_exit_overlays(
    signals: np.ndarray,
    df: pd.DataFrame,
    regimes: np.ndarray,
    exit_logic: Mapping[str, object] | None,
) -> np.ndarray:
    """Apply supported post-routing exit overlays from an exit_logic block."""
    if not exit_logic:
        return signals

    out = signals.copy()
    mature_trend_exit = exit_logic.get("mature_trend_exit")
    if isinstance(mature_trend_exit, Mapping):
        out = apply_mature_trend_exit(out, df, mature_trend_exit)

    bear_cooldown = exit_logic.get("bear_cooldown")
    if isinstance(bear_cooldown, Mapping):
        out = apply_bear_loss_cooldown(out, df, regimes, bear_cooldown)

    return out


def apply_mature_trend_exit(
    signals: np.ndarray,
    df: pd.DataFrame,
    config: Mapping[str, object],
) -> np.ndarray:
    """Exit mature trends after large MFE and completed daily EMA break."""
    if not bool(config.get("enabled", False)):
        return signals
    if not bool(config.get("use_completed_daily_bar", True)):
        raise ValueError("mature_trend_exit requires use_completed_daily_bar=true")

    activate_mfe = float(config.get("activate_mfe_pct", 0.0))
    daily_ema_period = int(config.get("daily_ema_period", 20))
    min_hold_bars = int(config.get("min_hold_bars_before_exit", 0))
    daily_ema = build_previous_completed_daily_ema(df, daily_ema_period)

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    out = signals.copy()

    position = 0
    entry_bar = 0
    entry_price = 0.0
    mfe = 0.0
    lockout_direction = 0

    for i, raw_signal in enumerate(signals):
        raw_target = _target_position(int(raw_signal), position)
        if lockout_direction and raw_target != lockout_direction:
            lockout_direction = 0
        if lockout_direction and raw_target == lockout_direction and position == 0:
            out[i] = 1
            continue

        forced_exit = False
        price = close[i]
        bars_held = i - entry_bar
        if position == 1 and entry_price > 0:
            mfe = max(mfe, high[i] / entry_price - 1.0)
            forced_exit = (
                bars_held >= min_hold_bars
                and mfe >= activate_mfe
                and np.isfinite(daily_ema[i])
                and price < daily_ema[i]
            )
        elif position == -1 and entry_price > 0:
            mfe = max(mfe, entry_price / max(low[i], 1e-12) - 1.0)
            forced_exit = (
                bars_held >= min_hold_bars
                and mfe >= activate_mfe
                and np.isfinite(daily_ema[i])
                and price > daily_ema[i]
            )

        if forced_exit:
            lockout_direction = position
            out[i] = 0
            position = 0
            entry_price = 0.0
            mfe = 0.0
            continue

        out[i] = int(raw_signal)
        new_position = _target_position(int(out[i]), position)
        if new_position != position and new_position in (1, -1):
            entry_bar = i
            entry_price = close[i]
            mfe = 0.0
        elif new_position == 0 and position != 0:
            entry_price = 0.0
            mfe = 0.0
        position = new_position

    return out


def apply_bear_loss_cooldown(
    signals: np.ndarray,
    df: pd.DataFrame,
    regimes: np.ndarray,
    config: Mapping[str, object],
) -> np.ndarray:
    """Block new BEAR entries after consecutive losing BEAR-entry trades."""
    if not bool(config.get("enabled", False)):
        return signals

    loss_streak_trigger = int(config.get("loss_streak", 2))
    cooldown_bars = int(config.get("cooldown_bars", 0))
    if cooldown_bars <= 0 and "cooldown_days" in config:
        cooldown_bars = int(config["cooldown_days"]) * BARS_PER_DAY_5M

    close = df["close"].to_numpy(dtype=float)
    out = signals.copy()

    position = 0
    entry_price = 0.0
    entry_regime = ""
    consecutive_bear_losses = 0
    cooldown_until = -1
    lockout_direction = 0

    for i, raw_signal in enumerate(signals):
        raw_target = _target_position(int(raw_signal), position)
        if lockout_direction and raw_target != lockout_direction:
            lockout_direction = 0

        in_bear_cooldown = regimes[i] == "BEAR" and i < cooldown_until
        if in_bear_cooldown and raw_target != position and raw_target in (1, -1):
            new_target = 0 if position in (1, -1) and raw_target == -position else position
            lockout_direction = raw_target
        elif lockout_direction and raw_target == lockout_direction and position == 0:
            new_target = 0
        else:
            new_target = raw_target

        if position != 0 and new_target != position:
            trade_return = _position_return(position, entry_price, close[i])
            if entry_regime == "BEAR":
                if trade_return < 0:
                    consecutive_bear_losses += 1
                else:
                    consecutive_bear_losses = 0
                if consecutive_bear_losses >= loss_streak_trigger:
                    cooldown_until = max(cooldown_until, i + cooldown_bars)
                    consecutive_bear_losses = 0
            entry_price = 0.0
            entry_regime = ""

        if regimes[i] == "BEAR" and i < cooldown_until and position != 0 and raw_target == -position:
            new_target = 0
            lockout_direction = raw_target

        out[i] = _signal_for_position(new_target) if new_target != position else _hold_signal(position)
        previous_position = position
        position = _target_position(int(out[i]), position)
        if position != previous_position and position in (1, -1):
            entry_price = close[i]
            entry_regime = str(regimes[i])
        elif position == 0 and previous_position != 0:
            entry_price = 0.0
            entry_regime = ""

    return out


def _timestamps(df: pd.DataFrame) -> pd.Series:
    if "datetime" in df.columns:
        return pd.to_datetime(df["datetime"])
    return pd.to_datetime(df["timestamp"])


def _target_position(signal: int, current_position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return current_position


def _signal_for_position(position: int) -> int:
    if position == 1:
        return 2
    if position == -1:
        return 3
    return 0


def _hold_signal(position: int) -> int:
    if position == 1:
        return 2
    if position == -1:
        return 3
    return 1


def _position_return(position: int, entry_price: float, exit_price: float) -> float:
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    if position == 1:
        return exit_price / entry_price - 1.0
    return entry_price / exit_price - 1.0
