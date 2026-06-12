from __future__ import annotations

import numpy as np
import pytest

from dex.strategies import (
    AdaptiveHybridStrategy,
    GridStrategy,
    HybridMeanRevMomentumStrategy,
    HybridStrategy,
    MultiTFEnsembleStrategy,
    PureActionStrategy,
    PureActionV2Strategy,
    ScalpStrategy,
    TrendFollowStrategy,
    TrendStrategy,
)
from dex.strategy_signals import generate_strategy_signals


@pytest.mark.parametrize(
    "strategy",
    [
        TrendStrategy(),
        ScalpStrategy(),
        PureActionStrategy(),
        PureActionV2Strategy(),
        HybridStrategy(),
        TrendFollowStrategy(),
        HybridMeanRevMomentumStrategy(),
        AdaptiveHybridStrategy(),
        GridStrategy(),
        MultiTFEnsembleStrategy(),
    ],
)
def test_strategy_generates_discrete_signals(strategy, sample_ohlcv) -> None:
    signals = generate_strategy_signals(strategy, sample_ohlcv, enable_short=True)

    assert len(signals) == len(sample_ohlcv)
    assert set(np.unique(signals)).issubset({0, 1, 2, 3})
