from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    n = 260
    close = 100 + np.sin(np.linspace(0, 12, n)) * 3 + np.linspace(0, 8, n)
    high = close + 1.5
    low = close - 1.5
    open_ = close + np.sin(np.linspace(0, 6, n)) * 0.4
    volume = np.full(n, 1000.0)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
