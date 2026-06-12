"""Shared strategy signal generation helpers."""

from __future__ import annotations

import inspect
from typing import Any

import numpy as np
import pandas as pd

from dex.strategies.grid import grid_signals_to_discrete


def generate_strategy_signals(
    strategy: Any,
    df: pd.DataFrame,
    enable_short: bool = True,
) -> np.ndarray:
    """Generate integer signals while tolerating strategy-specific method signatures."""
    method = strategy.generate_signals
    signature = inspect.signature(method)
    if "enable_short" in signature.parameters:
        signals = method(df, enable_short=enable_short)
    else:
        signals = method(df)

    signals = np.asarray(signals)
    if signals.dtype in (np.float32, np.float64, float):
        signals = grid_signals_to_discrete(signals, df["close"].values.astype(float))
    return signals.astype(int, copy=False)
