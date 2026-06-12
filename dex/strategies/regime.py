"""Regime-switching strategy that delegates to ranging or trending strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_adx
from dex.strategies.base import BaseStrategy
from dex.strategies.hybrid_mm import HybridMeanRevMomentumStrategy
from dex.strategies.trend_follow import TrendFollowStrategy


class RegimeStrategy(BaseStrategy):
    """Switch between mean-reversion and trend-following by current ADX."""

    def __init__(
        self,
        ranging_params: dict | None = None,
        trending_params: dict | None = None,
        adx_threshold: float = 25,
        enable_short: bool = True,
    ) -> None:
        self.adx_threshold = adx_threshold
        self.enable_short = enable_short

        rp = ranging_params or {}
        self.ranging = HybridMeanRevMomentumStrategy(
            rsi_period=rp.get("rsi_period", 14),
            rsi_low=rp.get("rsi_low", 30),
            rsi_high=rp.get("rsi_high", 70),
            ma_period=rp.get("ma_period", 20),
            atr_period=rp.get("atr_period", 14),
            atr_multiplier=rp.get("atr_multiplier", 2.0),
            max_hold_bars=rp.get("max_hold_bars", 24),
            enable_short=enable_short,
        )

        tp = trending_params or {}
        self.trending = TrendFollowStrategy(
            long_ma_period=tp.get("long_ma_period", 100),
            pull_ma_period=tp.get("pull_ma_period", 20),
            atr_period=tp.get("atr_period", 14),
            atr_multiplier=tp.get("atr_multiplier", 2.0),
            max_hold_bars=tp.get("max_hold_bars", 24),
            entry_zone=tp.get("entry_zone", 0.001),
            enable_short=enable_short,
        )

        self.window = max(
            getattr(self.ranging, "window", 20),
            getattr(self.trending, "long_ma_period", 100),
        )
        self.std_dev = getattr(self.ranging, "std_dev", 2.0)
        self._last_regime = "unknown"
        self._last_adx = 0.0

    @property
    def current_regime(self) -> str:
        return self._last_regime

    @property
    def current_adx(self) -> float:
        return self._last_adx

    def generate_signals(self, df: pd.DataFrame, enable_short: bool = False) -> np.ndarray:
        adx, _, _ = compute_adx(df, period=14)
        adx_now = float(adx[-1]) if len(adx) else 0.0
        self._last_adx = adx_now

        if adx_now > self.adx_threshold:
            self._last_regime = "trending"
            return self.trending.generate_signals(df)

        self._last_regime = "ranging"
        return self.ranging.generate_signals(df, enable_short=enable_short)
