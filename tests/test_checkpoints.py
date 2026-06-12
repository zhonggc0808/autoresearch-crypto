from __future__ import annotations

import numpy as np

from dex.checkpoints import (
    build_strategy_from_checkpoint,
    describe_strategy,
    generate_strategy_signals,
)
from dex.strategies import (
    HybridMeanRevMomentumStrategy,
    PureActionStrategy,
)


def test_builds_pureaction_strategy_from_checkpoint() -> None:
    checkpoint = {
        "strategy": "pureaction",
        "params": {
            "window": 15,
            "std_dev": 2.0,
            "atr_period": 7,
            "atr_multiplier": 2.5,
            "max_hold_bars": 12,
            "entry_zone": 0.0,
            "trend_ma_period": 50,
            "adx_threshold": None,
        },
    }

    strategy = build_strategy_from_checkpoint(checkpoint)

    assert isinstance(strategy, PureActionStrategy)
    assert strategy.window == 15
    assert strategy.enable_short is True
    assert strategy.trend_ma_period == 50


def test_builds_hybrid_mm_strategy_from_checkpoint() -> None:
    checkpoint = {
        "strategy": "hybrid_mm",
        "params": {
            "rsi_period": 5,
            "rsi_low": 28,
            "rsi_high": 72,
            "ma_period": 25,
            "atr_period": 12,
            "atr_multiplier": 3.5,
            "max_hold_bars": 48,
            "enable_short": False,
        },
    }

    strategy = build_strategy_from_checkpoint(checkpoint)

    assert isinstance(strategy, HybridMeanRevMomentumStrategy)
    assert strategy.rsi_period == 5
    assert strategy.enable_short is False


def test_generate_strategy_signals_handles_signature_differences(sample_ohlcv) -> None:
    checkpoint = {
        "strategy": "pureaction",
        "params": {
            "window": 15,
            "std_dev": 2.0,
            "atr_period": 7,
            "atr_multiplier": 2.5,
            "max_hold_bars": 12,
            "entry_zone": 0.0,
        },
    }
    strategy = build_strategy_from_checkpoint(checkpoint)

    signals = generate_strategy_signals(strategy, sample_ohlcv, enable_short=True)

    assert isinstance(signals, np.ndarray)
    assert len(signals) == len(sample_ohlcv)
    assert set(np.unique(signals)).issubset({0, 1, 2, 3})


def test_describes_strategy_with_core_parameters() -> None:
    checkpoint = {
        "strategy": "hybrid_mm",
        "params": {
            "rsi_period": 5,
            "rsi_low": 28,
            "rsi_high": 72,
            "ma_period": 25,
            "atr_multiplier": 3.5,
            "max_hold_bars": 48,
        },
    }
    strategy = build_strategy_from_checkpoint(checkpoint)

    lines = describe_strategy(strategy, "hybrid_mm")

    assert lines[0] == "策略模式: hybrid_mm"
    assert any("rsi_low=28" in line for line in lines)
    assert any("max_hold_bars=48" in line for line in lines)
