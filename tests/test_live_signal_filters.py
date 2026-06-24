from __future__ import annotations

import numpy as np
import pandas as pd

from dex.live.common import predict_signal


class AlwaysShortStrategy:
    window = 2
    std_dev = 2.0

    def generate_signals(self, df: pd.DataFrame) -> np.ndarray:
        return np.full(len(df), 3, dtype=int)


def test_regime_short_filter_blocks_non_bear_short() -> None:
    n = 10 * 288
    dts = pd.date_range("2026-01-01", periods=n, freq="5min")
    close = np.linspace(100.0, 200.0, n)
    df = pd.DataFrame(
        {
            "datetime": dts,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1.0,
        }
    )

    signal, info = predict_signal(
        AlwaysShortStrategy(),
        df,
        enable_short=True,
        signal_filter={"type": "regime_short_filter", "fast_days": 2, "slow_days": 3},
    )

    assert signal == 0
    assert info["raw_signal"] == 3
    assert info["filtered_signal"] == 0
    assert info["regime"] == "BULL"
