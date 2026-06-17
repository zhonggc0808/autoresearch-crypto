from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.build_market_intel_from_market_stats import build_market_intel


def test_build_market_intel_outputs_hourly_modes_from_completed_bars() -> None:
    datetimes = pd.date_range("2026-01-01 00:00:00", periods=36, freq="5min")
    close = np.full(36, 100.0)
    close[24:] = 104.0
    df = pd.DataFrame(
        {
            "datetime": datetimes,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(36, 1000.0),
        }
    )

    intel = build_market_intel(df, "ETHUSDT")

    assert intel["available_at"].iloc[0] == pd.Timestamp("2026-01-01 00:00:00")
    assert set(intel["mode"]) <= {"normal", "cautious", "block_new_entries"}
    assert "cautious" in set(intel["mode"]) or "block_new_entries" in set(intel["mode"])
    assert {"return_1h", "range_1h", "volatility_1h", "reason"} <= set(intel.columns)


def test_build_market_intel_uses_frozen_derivatives_stats() -> None:
    datetimes = pd.date_range("2026-01-01 00:00:00", periods=48, freq="5min")
    close = np.full(48, 100.0)
    df = pd.DataFrame(
        {
            "datetime": datetimes,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(48, 1000.0),
        }
    )
    derivatives = pd.DataFrame(
        {
            "available_at": pd.to_datetime(["2026-01-01 01:00:00", "2026-01-01 02:00:00"]),
            "funding_rate": [0.0, 0.002],
            "open_interest": [1000.0, 1100.0],
        }
    )

    intel = build_market_intel(df, "ETHUSDT", derivatives=derivatives)

    assert {"funding_rate", "funding_zscore", "open_interest", "oi_change_1h"} <= set(intel.columns)
    assert "block_new_entries" in set(intel["mode"])
