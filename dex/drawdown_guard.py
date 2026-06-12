"""Signal-level drawdown guard for backtest risk control."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE


@dataclass(frozen=True)
class DrawdownGuardStats:
    """Summary of drawdown guard activity."""

    total_bars: int
    max_dd_guard: float
    recovery_dd: float
    guard_entries: int
    guard_exits: int
    guarded_bars: int
    forced_closes: int
    max_observed_drawdown: float
    first_guard_time: str | None
    last_guard_time: str | None


class _AccountState:
    """Small account simulator mirroring StrategyEvaluator execution semantics."""

    def __init__(
        self,
        initial_capital: float,
        commission: float,
        slippage: float,
    ) -> None:
        self.capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.shares = 0.0
        self.position = 0
        self.entry_cost_basis = 0.0
        self.entry_price = 0.0

    def equity(self, price: float) -> float:
        if self.position == 1:
            return self.capital + self.shares * price
        if self.position == -1:
            return self.capital + abs(self.shares) * (self.entry_price - price)
        return self.capital

    def step(self, signal: int, price: float) -> float:
        if signal == 2:
            target_pos = 1
        elif signal == 3:
            target_pos = -1
        elif signal == 0:
            target_pos = 0
        else:
            target_pos = self.position

        if target_pos != self.position:
            if self.position == 1 and target_pos <= 0:
                exec_price = price * (1 - self.slippage)
                gross = self.shares * exec_price
                cost = gross * self.commission
                self.capital = gross - cost
                self.shares = 0.0
                self.position = 0

            elif self.position == -1 and target_pos >= 0:
                exec_price = price * (1 + self.slippage)
                buy_cost = abs(self.shares) * exec_price
                buy_cost_total = buy_cost * (1 + self.commission)
                self.capital = max(0.0, self.capital + self.entry_cost_basis - buy_cost_total)
                self.shares = 0.0
                self.position = 0

            if target_pos == 1 and self.position == 0 and self.capital > 0:
                exec_price = price * (1 + self.slippage)
                self.shares = self.capital * (1 - self.commission) / exec_price
                self.entry_cost_basis = self.capital
                self.entry_price = exec_price
                self.capital = 0.0
                self.position = 1

            elif target_pos == -1 and self.position == 0 and self.capital > 0:
                exec_price = price * (1 - self.slippage)
                self.shares = -(self.capital * (1 - self.commission) / exec_price)
                self.entry_cost_basis = self.capital
                self.entry_price = exec_price
                self.capital = self.capital * (1 - self.commission)
                self.position = -1

        return self.equity(price)


def apply_drawdown_guard(
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame | None = None,
    max_dd_guard: float = 0.25,
    recovery_dd: float = 0.15,
    initial_capital: float = INITIAL_CAPITAL,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
) -> tuple[np.ndarray, DrawdownGuardStats]:
    """Close exposure and pause entries when the strategy enters deep drawdown.

    Recovery is decided by a shadow account that keeps following the unguarded
    signal stream. This avoids the deadlock where a flat paused account cannot
    recover its own equity enough to resume trading.
    """
    if not 0 < recovery_dd < max_dd_guard < 1:
        raise ValueError("expected 0 < recovery_dd < max_dd_guard < 1")

    guarded = np.asarray(signals, dtype=int).copy()
    raw_signals = guarded.copy()
    px = np.asarray(prices, dtype=float)
    if len(guarded) != len(px):
        raise ValueError("signals and prices must have the same length")

    actual = _AccountState(initial_capital, commission, slippage)
    shadow = _AccountState(initial_capital, commission, slippage)
    actual_peak = initial_capital
    shadow_peak = initial_capital
    active = False

    guard_entries = 0
    guard_exits = 0
    guarded_bars = 0
    forced_closes = 0
    max_observed_drawdown = 0.0
    guard_mask = np.zeros(len(guarded), dtype=bool)

    for i, price in enumerate(px):
        actual_equity = actual.equity(price)
        actual_peak = max(actual_peak, actual_equity)
        actual_dd = _drawdown(actual_equity, actual_peak)

        shadow_equity = shadow.equity(price)
        shadow_peak = max(shadow_peak, shadow_equity)
        shadow_dd = _drawdown(shadow_equity, shadow_peak)

        if active and shadow_dd <= recovery_dd:
            active = False
            guard_exits += 1
            actual_peak = max(actual.equity(price), 1e-12)
            actual_dd = 0.0

        if not active and actual_dd >= max_dd_guard:
            active = True
            guard_entries += 1

        raw_signal = int(raw_signals[i])
        if active:
            guarded_bars += 1
            guard_mask[i] = True
            output_signal = 0 if actual.position != 0 else 1
            if output_signal == 0:
                forced_closes += 1
        else:
            output_signal = raw_signal

        guarded[i] = output_signal
        actual_equity = actual.step(output_signal, price)
        shadow.step(raw_signal, price)

        actual_peak = max(actual_peak, actual_equity)
        max_observed_drawdown = max(max_observed_drawdown, _drawdown(actual_equity, actual_peak))

    first_guard_time, last_guard_time = _mask_time_range(df, guard_mask)
    stats = DrawdownGuardStats(
        total_bars=len(guarded),
        max_dd_guard=max_dd_guard,
        recovery_dd=recovery_dd,
        guard_entries=guard_entries,
        guard_exits=guard_exits,
        guarded_bars=guarded_bars,
        forced_closes=forced_closes,
        max_observed_drawdown=max_observed_drawdown,
        first_guard_time=first_guard_time,
        last_guard_time=last_guard_time,
    )
    return guarded, stats


def _drawdown(equity: float, peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak)


def _mask_time_range(
    df: pd.DataFrame | None,
    mask: np.ndarray,
) -> tuple[str | None, str | None]:
    if df is None or not mask.any():
        return None, None

    if "datetime" in df.columns:
        raw = df["datetime"]
    elif "timestamp" in df.columns:
        raw = df["timestamp"]
    elif isinstance(df.index, pd.DatetimeIndex):
        datetimes = df.index
    else:
        return None, None

    if not isinstance(df.index, pd.DatetimeIndex):
        datetimes = pd.DatetimeIndex(pd.to_datetime(raw, errors="coerce"))
    matched_times = datetimes[mask]
    if len(matched_times) == 0:
        return None, None
    return str(matched_times[0]), str(matched_times[-1])
