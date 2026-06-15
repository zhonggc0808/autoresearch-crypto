"""Adaptive channel breakout — regime-dependent entry lookback.

Extends ChannelBreakoutTrendStrategy with per-regime lookback selection.
Uses the v2.1 regime filter (EMA50/200) to determine which lookback to
apply at each bar. All other logic (min_hold, permissions, exits)
inherits from ChannelBreakoutTrendStrategy unchanged.

Inspired by exp_0012 finding: L250 works well in BEAR but fails globally.
The fix: fast channel in BEAR, slow channel in NEUTRAL, standard in BULL.

Transition guards (exp_0014):
- lock_lookback_on_position: lock lookback at entry, don't change until flat
- transition_cooldown_bars: bars after regime flip before new entries
- regime_confirm_bars: bars a new regime must persist before lookback changes
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_atr, compute_ema
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy


class AdaptiveChannelBreakoutTrendStrategy(ChannelBreakoutTrendStrategy):
    """Channel breakout with regime-dependent entry lookback and transition guards.

    Args:
        entry_lookback: Default lookback.
        bull_entry_lookback: Lookback when regime == BULL (default: 375).
        bear_entry_lookback: Lookback when regime == BEAR (default: 250).
        neutral_entry_lookback: Lookback when regime == NEUTRAL (default: 500).
        regime_fast_days: EMA fast period (default: 50).
        regime_slow_days: EMA slow period (default: 200).
        lock_lookback_on_position: Lock lookback at entry until flat (default: False).
        transition_cooldown_bars: Bars after regime flip with no entries (default: 0).
        regime_confirm_bars: Bars new regime must persist before lookback changes (default: 0).
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
        lock_lookback_on_position: bool = False,
        transition_cooldown_bars: int = 0,
        regime_confirm_bars: int = 0,
        **kwargs,
    ) -> None:
        self.bull_entry_lookback = bull_entry_lookback
        self.bear_entry_lookback = bear_entry_lookback
        self.neutral_entry_lookback = neutral_entry_lookback
        self.regime_fast_days = regime_fast_days
        self.regime_slow_days = regime_slow_days
        self.lock_lookback_on_position = lock_lookback_on_position
        self.transition_cooldown_bars = transition_cooldown_bars
        self.regime_confirm_bars = regime_confirm_bars

        max_lb = max(entry_lookback, bull_entry_lookback, bear_entry_lookback, neutral_entry_lookback)
        super().__init__(entry_lookback=max_lb, **kwargs)

        self.regime_window = regime_slow_days * 288
        self.window = max(self.window, self.regime_window)
        self.warmup_bars = self.window

    def _get_regime_lookback(self, regime: str) -> int:
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

        # Pre-compute daily regime labels
        regimes = build_daily_regime_labels(
            df, fast_days=self.regime_fast_days, slow_days=self.regime_slow_days
        )

        # Pre-compute channels for each distinct lookback
        lookback_set = sorted(set([
            self.entry_lookback,
            self.bull_entry_lookback,
            self.bear_entry_lookback,
            self.neutral_entry_lookback,
        ]))
        channels = {}
        for lb in lookback_set:
            channels[lb] = {
                "high": pd.Series(high).rolling(lb, min_periods=lb).max().shift(1).to_numpy(),
                "low": pd.Series(low).rolling(lb, min_periods=lb).min().shift(1).to_numpy(),
            }

        position = 0
        entry_bar = 0
        entry_price = 0.0
        last_exit_bar = -self.cooldown_bars
        allow_short = self.enable_short and enable_short

        # Transition guard state
        current_lookback = self.entry_lookback
        prev_regime = "NEUTRAL"
        regime_flip_bar = 0
        regime_hold_count = 0
        entry_lookback_locked = None  # lookback locked at entry time

        for i in range(self.window, n):
            price = close[i]
            regime = str(regimes[i]) if i < len(regimes) else "NEUTRAL"

            # --- Transition guard: track regime stability ---
            if regime != prev_regime:
                regime_flip_bar = i
                regime_hold_count = 0
                prev_regime = regime
            else:
                regime_hold_count = i - regime_flip_bar

            # Determine active lookback for THIS bar
            regime_stable = regime_hold_count >= self.regime_confirm_bars
            if regime_stable:
                target_lookback = self._get_regime_lookback(regime)
            else:
                target_lookback = self._get_regime_lookback(prev_regime) if self.regime_confirm_bars > 0 else self._get_regime_lookback(regime)

            # If locked on position, use entry lookback
            if self.lock_lookback_on_position and entry_lookback_locked is not None:
                target_lookback = entry_lookback_locked
            else:
                current_lookback = target_lookback

            # Transition cooldown: no new entries after regime flip
            in_cooldown = (i - regime_flip_bar) < self.transition_cooldown_bars

            lb = target_lookback
            ch = channels[lb]
            ch_high = ch["high"][i]
            ch_low = ch["low"][i]

            # --- Conservative trigger during transition ---
            # If we're in a transition period (regime changed but not yet confirmed),
            # use the HIGHER long trigger and LOWER short trigger across old and new
            if not regime_stable and self.regime_confirm_bars > 0:
                new_lb = self._get_regime_lookback(regime)
                new_ch = channels[new_lb]
                ch_high = max(ch_high, new_ch["high"][i])  # harder to trigger long
                ch_low = min(ch_low, new_ch["low"][i])      # harder to trigger short

            can_flip = i - entry_bar >= self.min_hold_bars
            can_enter = i - last_exit_bar >= self.cooldown_bars and not in_cooldown

            # --- Trend filters ---
            trend_allows_long = True
            trend_allows_short = True
            if self.trend_ma_period > 0:
                trend_ma = compute_ema(close, self.trend_ma_period)
                slope = 0.0
                if self.trend_slope_lookback > 0 and i - self.trend_slope_lookback >= 0:
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
            long_break = self.enable_long and trend_allows_long and price > long_trigger
            short_break = allow_short and trend_allows_short and price < short_trigger

            # --- Position management ---
            if position == 1:
                stop_hit = self.emergency_stop_pct > 0 and price <= entry_price * (1.0 - self.emergency_stop_pct)
                if stop_hit:
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    entry_lookback_locked = None
                    continue
                if can_flip and short_break:
                    signals[i] = 3
                    position = -1
                    entry_bar = i
                    entry_price = price
                    entry_lookback_locked = lb if self.lock_lookback_on_position else None
                    continue
                signals[i] = 2
                continue

            if position == -1:
                stop_hit = self.emergency_stop_pct > 0 and price >= entry_price * (1.0 + self.emergency_stop_pct)
                if stop_hit:
                    signals[i] = 0
                    position = 0
                    last_exit_bar = i
                    entry_lookback_locked = None
                    continue
                if can_flip and long_break:
                    signals[i] = 2
                    position = 1
                    entry_bar = i
                    entry_price = price
                    entry_lookback_locked = lb if self.lock_lookback_on_position else None
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
                entry_lookback_locked = lb if self.lock_lookback_on_position else None
            elif short_break:
                signals[i] = 3
                position = -1
                entry_bar = i
                entry_price = price
                entry_lookback_locked = lb if self.lock_lookback_on_position else None

        return signals
