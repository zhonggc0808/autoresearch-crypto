"""Adaptive channel breakout — regime-dependent entry lookback.

Extends ChannelBreakoutTrendStrategy with per-regime lookback selection.
Uses the v2.1 regime filter (EMA50/200) to determine which lookback to
apply at each bar. All other logic (min_hold, permissions, exits)
inherits from ChannelBreakoutTrendStrategy unchanged.

Inspired by exp_0012 finding: L250 works well in BEAR but fails globally.
The fix: fast channel in BEAR, slow channel in NEUTRAL, standard in BULL.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_adx, compute_atr, compute_ema
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import BaseStrategy
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy


class AdaptiveChannelBreakoutTrendStrategy(ChannelBreakoutTrendStrategy):
    """Channel breakout with regime-dependent entry lookback.

    Uses separate entry lookback values per regime (BULL/BEAR/NEUTRAL)
    instead of a single global entry_lookback. The lookback is selected
    per bar based on the daily EMA50/200 regime label.

    Args:
        entry_lookback: Default lookback (used if regime labels unavailable).
        bull_entry_lookback: Lookback when regime == BULL (default: 375).
        bear_entry_lookback: Lookback when regime == BEAR (default: 250).
        neutral_entry_lookback: Lookback when regime == NEUTRAL (default: 500).
        regime_fast_days: EMA fast period for regime detection (default: 50).
        regime_slow_days: EMA slow period for regime detection (default: 200).
        All other args match ChannelBreakoutTrendStrategy.
    """

    def __init__(
        self,
        entry_lookback: int = 375,
        bull_entry_lookback: int = 375,
        bear_entry_lookback: int = 250,
        neutral_entry_lookback: int = 500,
        regime_fast_days: int = 50,
        regime_slow_days: int = 200,
        **kwargs,
    ) -> None:
        # Store per-regime lookbacks before parent init
        self.bull_entry_lookback = bull_entry_lookback
        self.bear_entry_lookback = bear_entry_lookback
        self.neutral_entry_lookback = neutral_entry_lookback
        self.regime_fast_days = regime_fast_days
        self.regime_slow_days = regime_slow_days

        # Parent __init__ sets self.entry_lookback, self.window, etc.
        # We pass the MAX lookback as entry_lookback so window is wide enough
        max_lb = max(entry_lookback, bull_entry_lookback, bear_entry_lookback, neutral_entry_lookback)
        super().__init__(entry_lookback=max_lb, **kwargs)

        # Override window to account for regime filter warmup
        self.regime_window = regime_slow_days * 288  # bars for EMA200 warmup
        self.window = max(self.window, self.regime_window)
        self.warmup_bars = self.window

    def _get_regime_lookback(self, regime: str) -> int:
        """Return the entry lookback for a given regime label."""
        return {
            "BULL": self.bull_entry_lookback,
            "BEAR": self.bear_entry_lookback,
            "NEUTRAL": self.neutral_entry_lookback,
        }.get(regime.upper(), self.entry_lookback)

    def generate_signals(self, df: pd.DataFrame, enable_short: bool = True) -> np.ndarray:
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        signals = np.ones(n, dtype=int)
        if n <= self.window:
            return signals

        # Pre-compute daily regime labels on the full df
        regimes = build_daily_regime_labels(
            df, fast_days=self.regime_fast_days, slow_days=self.regime_slow_days
        )

        # Pre-compute rolling channels for each distinct lookback value
        lookback_set = sorted(set([
            self.entry_lookback,
            self.bull_entry_lookback,
            self.bear_entry_lookback,
            self.neutral_entry_lookback,
        ]))

        channels = {}
        for lb in lookback_set:
            channels[lb] = {
                "high": (
                    pd.Series(high).rolling(lb, min_periods=lb).max().shift(1).to_numpy()
                ),
                "low": (
                    pd.Series(low).rolling(lb, min_periods=lb).min().shift(1).to_numpy()
                ),
            }

        position = 0
        entry_bar = 0
        entry_price = 0.0
        last_exit_bar = -self.cooldown_bars
        allow_short = self.enable_short and enable_short

        for i in range(self.window, n):
            price = close[i]
            regime = str(regimes[i]) if i < len(regimes) else "NEUTRAL"
            lb = self._get_regime_lookback(regime)
            ch = channels[lb]
            ch_high = ch["high"][i]
            ch_low = ch["low"][i]

            can_flip = i - entry_bar >= self.min_hold_bars
            can_enter = i - last_exit_bar >= self.cooldown_bars
            trend_allows_long = True
            trend_allows_short = True

            if self.trend_ma_period > 0:
                trend_ma = compute_ema(close, self.trend_ma_period)
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

            atr_buffer = 0.0
            if self.breakout_atr_buffer > 0:
                atr = compute_atr(df, self.atr_period)
                atr_buffer = self.breakout_atr_buffer * atr[i]

            long_trigger = ch_high * (1.0 + self.breakout_buffer_pct) + atr_buffer
            short_trigger = ch_low * (1.0 - self.breakout_buffer_pct) - atr_buffer
            long_break = (
                self.enable_long
                and trend_allows_long
                and price > long_trigger
            )
            short_break = (
                allow_short
                and trend_allows_short
                and price < short_trigger
            )

            if position == 1:
                stop_hit = self.emergency_stop_pct > 0 and price <= entry_price * (1.0 - self.emergency_stop_pct)
                if stop_hit:
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
                stop_hit = self.emergency_stop_pct > 0 and price >= entry_price * (1.0 + self.emergency_stop_pct)
                if stop_hit:
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
