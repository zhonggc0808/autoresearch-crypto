"""Directional trend participation strategy.

This strategy is designed for paper/live trials where exposure should follow
the dominant market direction. It favours staying with confirmed trends and
treats pullbacks as re-entry opportunities rather than as the primary source
of edge.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_adx, compute_atr, compute_ema, compute_rsi
from dex.strategies.base import BaseStrategy


class LongBiasTrendStrategy(BaseStrategy):
    """Directional EMA trend strategy with ATR risk exits.

    Signal encoding follows the project convention: 0=close, 1=hold,
    2=long, 3=short. Shorts are only generated when both the strategy and
    caller explicitly allow them.
    """

    def __init__(
        self,
        fast_ma_period: int = 50,
        slow_ma_period: int = 200,
        macro_ma_period: int = 400,
        pullback_ma_period: int = 20,
        breakout_lookback: int = 48,
        adx_period: int = 14,
        adx_threshold: float = 18.0,
        slope_lookback: int = 12,
        macro_slope_lookback: int = 48,
        min_trend_slope: float = 0.0,
        min_macro_slope: float = -0.001,
        require_macro_uptrend: bool | None = None,
        require_macro_alignment: bool = True,
        rsi_period: int = 14,
        entry_rsi_min: float = 45.0,
        entry_rsi_max: float = 85.0,
        exit_rsi: float = 38.0,
        pullback_zone: float = 0.006,
        breakout_buffer: float = 0.001,
        atr_period: int = 14,
        atr_multiplier: float = 3.0,
        hard_stop_pct: float = 0.045,
        exit_ma_buffer: float = 0.004,
        max_hold_bars: int = 0,
        cooldown_bars: int = 12,
        enable_short: bool = True,
        take_profit_pct: float = 0.0,
        stop_loss_pct: float | None = None,
    ) -> None:
        self.fast_ma_period = fast_ma_period
        self.slow_ma_period = slow_ma_period
        self.macro_ma_period = macro_ma_period
        self.pullback_ma_period = pullback_ma_period
        self.breakout_lookback = breakout_lookback
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.slope_lookback = slope_lookback
        self.macro_slope_lookback = macro_slope_lookback
        self.min_trend_slope = min_trend_slope
        self.min_macro_slope = min_macro_slope
        if require_macro_uptrend is not None:
            require_macro_alignment = require_macro_uptrend
        self.require_macro_alignment = require_macro_alignment
        self.rsi_period = rsi_period
        self.entry_rsi_min = entry_rsi_min
        self.entry_rsi_max = entry_rsi_max
        self.exit_rsi = exit_rsi
        self.pullback_zone = pullback_zone
        self.breakout_buffer = breakout_buffer
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.hard_stop_pct = hard_stop_pct
        self.exit_ma_buffer = exit_ma_buffer
        self.max_hold_bars = max_hold_bars
        self.cooldown_bars = cooldown_bars
        self.enable_short = enable_short
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = hard_stop_pct if stop_loss_pct is None else stop_loss_pct

        self.window = max(
            fast_ma_period,
            slow_ma_period,
            macro_ma_period,
            pullback_ma_period,
            breakout_lookback,
            adx_period * 2,
            slope_lookback,
            macro_slope_lookback,
            rsi_period,
            atr_period,
        )
        self.std_dev = 2.0

    def generate_signals(self, df: pd.DataFrame, enable_short: bool = False) -> np.ndarray:
        """Generate directional trend participation signals."""
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        fast_ma = compute_ema(close, self.fast_ma_period)
        slow_ma = compute_ema(close, self.slow_ma_period)
        macro_ma = compute_ema(close, self.macro_ma_period)
        pullback_ma = compute_ema(close, self.pullback_ma_period)
        adx, plus_di, minus_di = compute_adx(df, self.adx_period)
        rsi = compute_rsi(close, self.rsi_period)
        atr = compute_atr(df, self.atr_period)

        signals = np.ones(n, dtype=int)
        position = 0
        entry_bar = 0
        entry_price = 0.0
        highest_after_entry = 0.0
        lowest_after_entry = float("inf")
        allow_short = self.enable_short and enable_short
        last_exit_bar = -self.cooldown_bars

        for i in range(self.window, n):
            price = close[i]
            prev_price = close[i - 1]
            fast_prev = fast_ma[max(0, i - self.slope_lookback)]
            fast_slope = (fast_ma[i] / fast_prev - 1.0) if fast_prev > 0 else 0.0
            macro_prev = macro_ma[max(0, i - self.macro_slope_lookback)]
            macro_slope = (macro_ma[i] / macro_prev - 1.0) if macro_prev > 0 else 0.0

            trend_strength_ok = adx[i] >= self.adx_threshold or fast_slope > self.min_trend_slope
            macro_slope_gate = abs(self.min_macro_slope)
            macro_uptrend = (
                price > macro_ma[i]
                and slow_ma[i] > macro_ma[i] * (1.0 - self.exit_ma_buffer)
                and macro_slope >= self.min_macro_slope
            )
            macro_downtrend = (
                price < macro_ma[i]
                and slow_ma[i] < macro_ma[i] * (1.0 + self.exit_ma_buffer)
                and macro_slope <= -macro_slope_gate
            )
            macro_allows_long = macro_uptrend or not self.require_macro_alignment
            macro_allows_short = macro_downtrend or not self.require_macro_alignment
            uptrend = (
                macro_allows_long
                and fast_ma[i] > slow_ma[i]
                and price > slow_ma[i] * (1.0 - self.exit_ma_buffer)
                and plus_di[i] >= minus_di[i] * 0.85
                and trend_strength_ok
            )
            downtrend = (
                macro_allows_short
                and fast_ma[i] < slow_ma[i]
                and price < slow_ma[i] * (1.0 + self.exit_ma_buffer)
                and minus_di[i] > plus_di[i]
                and trend_strength_ok
            )

            if position == 1:
                if high[i] > highest_after_entry:
                    highest_after_entry = high[i]

                atr_stop = highest_after_entry - self.atr_multiplier * atr[i]
                hard_stop = entry_price * (1.0 - self.hard_stop_pct)
                ma_break = price < slow_ma[i] * (1.0 - self.exit_ma_buffer)
                momentum_break = rsi[i] < self.exit_rsi and price < fast_ma[i]
                trend_break = fast_ma[i] < slow_ma[i]
                time_exit = self.max_hold_bars > 0 and i - entry_bar >= self.max_hold_bars

                if (
                    price < atr_stop
                    or price < hard_stop
                    or ma_break
                    or momentum_break
                    or trend_break
                    or time_exit
                ):
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    continue

                signals[i] = 2
                continue

            if position == -1:
                if low[i] < lowest_after_entry:
                    lowest_after_entry = low[i]

                atr_stop = lowest_after_entry + self.atr_multiplier * atr[i]
                hard_stop = entry_price * (1.0 + self.hard_stop_pct)
                ma_break = price > slow_ma[i] * (1.0 + self.exit_ma_buffer)
                trend_break = fast_ma[i] > slow_ma[i]
                time_exit = self.max_hold_bars > 0 and i - entry_bar >= self.max_hold_bars

                if price > atr_stop or price > hard_stop or ma_break or trend_break or time_exit:
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    continue

                signals[i] = 3
                continue

            if position != 0:
                continue
            if i - last_exit_bar < self.cooldown_bars:
                continue

            rsi_ok = self.entry_rsi_min <= rsi[i] <= self.entry_rsi_max
            recent_high = np.max(high[i - self.breakout_lookback : i])
            breakout = price > recent_high * (1.0 + self.breakout_buffer)
            trend_reclaim = price > fast_ma[i] and prev_price <= fast_ma[i - 1]
            pullback_reclaim = (
                low[i] <= pullback_ma[i] * (1.0 + self.pullback_zone)
                and price > pullback_ma[i]
                and price > prev_price
            )
            steady_participation = (
                price > fast_ma[i]
                and fast_ma[i] > fast_ma[i - 1]
                and fast_slope >= self.min_trend_slope
            )

            if (
                uptrend
                and rsi_ok
                and (steady_participation or breakout or trend_reclaim or pullback_reclaim)
            ):
                signals[i] = 2
                position = 1
                entry_bar = i
                entry_price = price
                highest_after_entry = high[i]
                continue

            if not allow_short:
                continue

            short_rsi_ok = (100.0 - self.entry_rsi_max) <= rsi[i] <= (100.0 - self.entry_rsi_min)
            recent_low = np.min(low[i - self.breakout_lookback : i])
            breakdown = price < recent_low * (1.0 - self.breakout_buffer)
            short_reclaim = price < fast_ma[i] and prev_price >= fast_ma[i - 1]
            short_steady = (
                price < fast_ma[i]
                and fast_ma[i] < fast_ma[i - 1]
                and fast_slope <= -self.min_trend_slope
            )

            if downtrend and short_rsi_ok and (short_steady or breakdown or short_reclaim):
                signals[i] = 3
                position = -1
                entry_bar = i
                entry_price = price
                lowest_after_entry = low[i]

        return signals


DirectionalTrendStrategy = LongBiasTrendStrategy
