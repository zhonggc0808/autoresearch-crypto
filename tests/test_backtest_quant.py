from __future__ import annotations

import numpy as np

from backtest_quant import buy_hold_signals, normalize_signals_for_position_mode


def test_normalize_signals_for_long_only_removes_short_entries() -> None:
    signals = np.array([1, 2, 3, 1, 0, 3])

    normalized = normalize_signals_for_position_mode(signals, long_only=True)

    assert normalized.tolist() == [1, 2, 1, 1, 0, 1]
    assert signals.tolist() == [1, 2, 3, 1, 0, 3]


def test_normalize_signals_leaves_long_short_mode_unchanged() -> None:
    signals = np.array([1, 2, 3, 0])

    normalized = normalize_signals_for_position_mode(signals, long_only=False)

    assert normalized.tolist() == [1, 2, 3, 0]


def test_buy_hold_signals_open_once_then_hold() -> None:
    assert buy_hold_signals(5).tolist() == [2, 1, 1, 1, 1]
    assert buy_hold_signals(0).tolist() == []
