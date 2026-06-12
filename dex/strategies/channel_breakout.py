"""Low-frequency Donchian channel breakout trend strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_adx, compute_atr, compute_ema
from dex.strategies.base import BaseStrategy


class ChannelBreakoutTrendStrategy(BaseStrategy):
    """Flip long/short on slow Donchian channel breakouts.

    The strategy is intentionally low frequency: it enters long when price
    breaks the previous channel high, enters short when price breaks the
    previous channel low, and otherwise keeps the current position. This is a
    better fit for large directional regimes with violent counter-trend
    rebounds than tight trailing-stop systems.
    """

    def __init__(
        self,
        entry_lookback: int = 8000,
        exit_lookback: int = 0,
        min_hold_bars: int = 0,
        cooldown_bars: int = 0,
        emergency_stop_pct: float = 0.0,
        breakout_buffer_pct: float = 0.0,
        breakout_atr_buffer: float = 0.0,
        atr_period: int = 14,
        trend_ma_period: int = 0,
        trend_slope_lookback: int = 0,
        min_trend_slope: float = 0.0,
        trend_buffer_pct: float = 0.0,
        adx_period: int = 14,
        adx_threshold: float = 0.0,
        require_di_alignment: bool = False,
        enable_long: bool = True,
        enable_short: bool = True,
        enable_short_alias: bool | None = None,
        take_profit_pct: float = 0.0,
        stop_loss_pct: float = 0.0,
        max_hold_bars: int = 0,
    ) -> None:
        if enable_short_alias is not None:
            enable_short = enable_short_alias
        self.entry_lookback = entry_lookback
        self.exit_lookback = exit_lookback
        self.min_hold_bars = min_hold_bars
        self.cooldown_bars = cooldown_bars
        self.emergency_stop_pct = emergency_stop_pct
        self.breakout_buffer_pct = breakout_buffer_pct
        self.breakout_atr_buffer = breakout_atr_buffer
        self.atr_period = atr_period
        self.trend_ma_period = trend_ma_period
        self.trend_slope_lookback = trend_slope_lookback
        self.min_trend_slope = min_trend_slope
        self.trend_buffer_pct = trend_buffer_pct
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.require_di_alignment = require_di_alignment
        self.enable_long = enable_long
        self.enable_short = enable_short
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_bars = max_hold_bars

        atr_window = atr_period if breakout_atr_buffer > 0 else 0
        adx_window = adx_period * 2 if adx_threshold > 0 or require_di_alignment else 0
        self.window = max(
            entry_lookback,
            exit_lookback,
            trend_ma_period,
            trend_slope_lookback,
            atr_window,
            adx_window,
        )
        self.warmup_bars = self.window
        self.std_dev = 2.0

    def generate_signals(self, df: pd.DataFrame, enable_short: bool = True) -> np.ndarray:
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        signals = np.ones(n, dtype=int)
        if n <= self.window:
            return signals

        rolling_high = (
            pd.Series(high).rolling(self.entry_lookback, min_periods=self.entry_lookback).max()
        )
        rolling_low = (
            pd.Series(low).rolling(self.entry_lookback, min_periods=self.entry_lookback).min()
        )
        channel_high = rolling_high.shift(1).to_numpy()
        channel_low = rolling_low.shift(1).to_numpy()

        exit_high = exit_low = None
        if self.exit_lookback > 0:
            exit_high = (
                pd.Series(high).rolling(self.exit_lookback, min_periods=self.exit_lookback).max()
            )
            exit_low = (
                pd.Series(low).rolling(self.exit_lookback, min_periods=self.exit_lookback).min()
            )
            exit_high = exit_high.shift(1).to_numpy()
            exit_low = exit_low.shift(1).to_numpy()

        atr = None
        if self.breakout_atr_buffer > 0:
            atr = compute_atr(df, self.atr_period)

        trend_ma = None
        if self.trend_ma_period > 0:
            trend_ma = compute_ema(close, self.trend_ma_period)

        adx = plus_di = minus_di = None
        if self.adx_threshold > 0 or self.require_di_alignment:
            adx, plus_di, minus_di = compute_adx(df, self.adx_period)

        position = 0
        entry_bar = 0
        entry_price = 0.0
        last_exit_bar = -self.cooldown_bars
        allow_short = self.enable_short and enable_short

        for i in range(self.window, n):
            price = close[i]
            can_flip = i - entry_bar >= self.min_hold_bars
            can_enter = i - last_exit_bar >= self.cooldown_bars
            trend_allows_long = True
            trend_allows_short = True
            if trend_ma is not None:
                slope = 0.0
                if self.trend_slope_lookback > 0:
                    prev_idx = max(0, i - self.trend_slope_lookback)
                    prev_ma = trend_ma[prev_idx]
                    slope = (trend_ma[i] / prev_ma - 1.0) if prev_ma > 0 else 0.0

                trend_allows_long = price > trend_ma[i] * (1.0 + self.trend_buffer_pct)
                trend_allows_short = price < trend_ma[i] * (1.0 - self.trend_buffer_pct)
                if self.min_trend_slope > 0:
                    trend_allows_long = trend_allows_long and slope >= self.min_trend_slope
                    trend_allows_short = trend_allows_short and slope <= -self.min_trend_slope

            trend_strength_ok = True
            if adx is not None and self.adx_threshold > 0:
                trend_strength_ok = adx[i] >= self.adx_threshold

            di_allows_long = True
            di_allows_short = True
            if plus_di is not None and minus_di is not None and self.require_di_alignment:
                di_allows_long = plus_di[i] >= minus_di[i]
                di_allows_short = minus_di[i] >= plus_di[i]

            atr_buffer = self.breakout_atr_buffer * atr[i] if atr is not None else 0.0
            long_trigger = channel_high[i] * (1.0 + self.breakout_buffer_pct) + atr_buffer
            short_trigger = channel_low[i] * (1.0 - self.breakout_buffer_pct) - atr_buffer
            long_break = (
                self.enable_long
                and trend_strength_ok
                and trend_allows_long
                and di_allows_long
                and price > long_trigger
            )
            short_break = (
                allow_short
                and trend_strength_ok
                and trend_allows_short
                and di_allows_short
                and price < short_trigger
            )

            if position == 1:
                stop_hit = self.emergency_stop_pct > 0 and price <= entry_price * (
                    1.0 - self.emergency_stop_pct
                )
                exit_hit = exit_low is not None and price < exit_low[i]
                if stop_hit:
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    continue
                if exit_hit and can_flip:
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    continue
                if can_flip and short_break:
                    signals[i] = 3
                    position = -1
                    entry_bar = i
                    entry_price = price
                    continue
                signals[i] = 2
                continue

            if position == -1:
                stop_hit = self.emergency_stop_pct > 0 and price >= entry_price * (
                    1.0 + self.emergency_stop_pct
                )
                exit_hit = exit_high is not None and price > exit_high[i]
                if stop_hit:
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    continue
                if exit_hit and can_flip:
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    continue
                if can_flip and long_break:
                    signals[i] = 2
                    position = 1
                    entry_bar = i
                    entry_price = price
                    continue
                signals[i] = 3
                continue

            if not can_enter:
                continue
            if long_break:
                signals[i] = 2
                position = 1
                entry_bar = i
                entry_price = price
            elif short_break:
                signals[i] = 3
                position = -1
                entry_bar = i
                entry_price = price

        return signals
