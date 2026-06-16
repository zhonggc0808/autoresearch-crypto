from __future__ import annotations

import numpy as np
import pandas as pd

from dex.exit_overlays import (
    apply_bear_loss_cooldown,
    apply_exit_overlays,
    apply_mature_trend_exit,
    build_previous_completed_daily_ema,
)


def _ohlcv(close_values: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    close = np.asarray(close_values, dtype=float)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(start, periods=len(close), freq="5min"),
            "datetime": pd.date_range(start, periods=len(close), freq="5min"),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        }
    )


def test_previous_completed_daily_ema_uses_prior_day_close_only() -> None:
    day1 = [100.0] * 288
    day2 = [120.0] * 288
    day3 = [50.0] * 12
    df = _ohlcv(day1 + day2 + day3)

    ema = build_previous_completed_daily_ema(df, period=20)

    assert np.isnan(ema[0])
    assert ema[288] == 100.0
    assert ema[576] > 100.0
    assert ema[576] < 120.0


def test_mature_trend_exit_closes_long_after_large_mfe_breaks_previous_daily_ema() -> None:
    df = _ohlcv([100.0] * 288 + [150.0] * 288 + [330.0, 340.0, 190.0, 90.0, 90.0])
    signals = np.ones(len(df), dtype=int)
    signals[288] = 2
    signals[289:] = 2

    out = apply_mature_trend_exit(
        signals,
        df,
        {
            "enabled": True,
            "activate_mfe_pct": 1.20,
            "daily_ema_period": 20,
            "use_completed_daily_bar": True,
        },
    )

    assert out[578] == 2
    assert out[579] == 0
    assert out[580] == 1


def test_bear_loss_cooldown_blocks_new_bear_entry_but_allows_existing_exit() -> None:
    df = _ohlcv([100.0, 90.0, 100.0, 90.0, 100.0, 100.0, 100.0, 100.0])
    regimes = np.array(["BEAR"] * len(df))
    signals = np.array([2, 0, 2, 0, 2, 2, 0, 1])

    out = apply_bear_loss_cooldown(
        signals,
        df,
        regimes,
        {
            "enabled": True,
            "loss_streak": 2,
            "cooldown_bars": 3,
        },
    )

    assert out[1] == 0
    assert out[3] == 0
    assert out[4] == 1
    assert out[5] == 1
    assert out[6] == 1


def test_apply_exit_overlays_runs_mature_exit_then_bear_cooldown() -> None:
    df = _ohlcv([100.0] * 288 + [150.0] * 288 + [330.0, 340.0, 190.0, 90.0, 90.0])
    signals = np.ones(len(df), dtype=int)
    signals[288:] = 2
    regimes = np.array(["BULL"] * len(df))

    out = apply_exit_overlays(
        signals,
        df,
        regimes,
        {
            "mature_trend_exit": {
                "enabled": True,
                "activate_mfe_pct": 1.20,
                "daily_ema_period": 20,
                "use_completed_daily_bar": True,
            },
            "bear_cooldown": {
                "enabled": True,
                "loss_streak": 2,
                "cooldown_bars": 3,
            },
        },
    )

    assert out[579] == 0
