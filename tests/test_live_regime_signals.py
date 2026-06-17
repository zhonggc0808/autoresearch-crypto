from __future__ import annotations

import numpy as np
import pandas as pd

from dex.live.signals import generate_live_regime_channel_breakout_signal
from dex.regime_permissions import RiskOffConfig


class StaticStrategy:
    def __init__(self, signals: np.ndarray, enable_short: bool = True) -> None:
        self.signals = signals
        self.enable_short = enable_short

    def generate_signals(self, df: pd.DataFrame) -> np.ndarray:
        return self.signals[: len(df)]


def _ohlcv(close_values: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    close = np.asarray(close_values, dtype=float)
    timestamps = pd.date_range(start, periods=len(close), freq="5min")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "datetime": timestamps,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        }
    )


def test_live_regime_signal_can_disable_exit_overlays() -> None:
    df = _ohlcv([100.0] * 288 + [150.0] * 288 + [330.0, 340.0, 190.0, 90.0])
    signals = np.ones(len(df), dtype=int)
    signals[288:] = 2
    strategy = StaticStrategy(signals)
    cfg = RiskOffConfig(allow_long=True, allow_short=True)
    exit_logic = {
        "mature_trend_exit": {
            "enabled": True,
            "activate_mfe_pct": 1.20,
            "daily_ema_period": 20,
            "use_completed_daily_bar": True,
        }
    }

    signal_id, diag = generate_live_regime_channel_breakout_signal(
        df,
        strategy,
        strategy,
        strategy,
        cfg,
        cfg,
        cfg,
        "permission_based",
        50,
        200,
        exit_logic=exit_logic,
        exit_overlays_enabled=False,
    )

    assert signal_id == 2
    assert diag["permission_signal"] == 2
    assert diag["exit_overlays_enabled"] is False
    assert diag["exit_overlay_changed"] is False


def test_live_regime_signal_applies_exit_overlays() -> None:
    df = _ohlcv([100.0] * 288 + [150.0] * 288 + [330.0, 340.0, 190.0, 90.0])
    signals = np.ones(len(df), dtype=int)
    signals[288:] = 2
    strategy = StaticStrategy(signals)
    cfg = RiskOffConfig(allow_long=True, allow_short=True)

    signal_id, diag = generate_live_regime_channel_breakout_signal(
        df,
        strategy,
        strategy,
        strategy,
        cfg,
        cfg,
        cfg,
        "permission_based",
        50,
        200,
        exit_logic={
            "mature_trend_exit": {
                "enabled": True,
                "activate_mfe_pct": 1.20,
                "daily_ema_period": 20,
                "use_completed_daily_bar": True,
            }
        },
        exit_overlays_enabled=True,
    )

    assert signal_id == 0
    assert diag["permission_signal"] == 2
    assert diag["exit_overlays_enabled"] is True
    assert diag["exit_overlay_changed"] is True
    assert diag["exit_overlay_changed_last"] is True
    assert diag["permission_reason"].startswith("exit_overlay")
