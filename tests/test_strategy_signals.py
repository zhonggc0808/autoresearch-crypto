from __future__ import annotations

import numpy as np

from dex.strategies import GridStrategy, PureActionStrategy
from dex.strategy_signals import generate_strategy_signals


def test_generate_strategy_signals_is_available_from_runtime_module(sample_ohlcv) -> None:
    strategy = PureActionStrategy()

    signals = generate_strategy_signals(strategy, sample_ohlcv, enable_short=True)

    assert len(signals) == len(sample_ohlcv)
    assert set(np.unique(signals)).issubset({0, 1, 2, 3})


def test_generate_strategy_signals_normalizes_grid_float_positions(sample_ohlcv) -> None:
    strategy = GridStrategy()

    signals = generate_strategy_signals(strategy, sample_ohlcv, enable_short=True)

    assert signals.dtype.kind in {"i", "u"}
    assert set(np.unique(signals)).issubset({0, 1, 2, 3})
