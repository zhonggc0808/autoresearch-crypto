from __future__ import annotations

import numpy as np
import pytest

from dex.config import BARS_PER_DAY_5M
from dex.execution_safety import apply_n2b_1d_block_reversals_only


def test_n2b_patch_blocks_direct_reversal_to_flat_only():
    signals = np.array([2, 1, 3, 1], dtype=int)
    regimes = np.array(["NEUTRAL", "BEAR", "BEAR", "BEAR"], dtype=object)

    result = apply_n2b_1d_block_reversals_only(signals, regimes, warmup_bars=3)

    assert result.signals.tolist() == [2, 1, 0, 1]
    assert result.diagnostics["blocked_actions_total"] == 1
    assert result.diagnostics["blocked_reversals_total"] == 1
    assert result.diagnostics["blocked_long_to_short_reversals"] == 1
    assert result.diagnostics["blocked_short_to_long_reversals"] == 0
    assert result.diagnostics["blocked_entries_total"] == 0


def test_n2b_patch_blocks_short_to_long_reversal_to_flat_only():
    signals = np.array([3, 1, 2, 1], dtype=int)
    regimes = np.array(["NEUTRAL", "BEAR", "BEAR", "BEAR"], dtype=object)

    result = apply_n2b_1d_block_reversals_only(signals, regimes, warmup_bars=3)

    assert result.signals.tolist() == [3, 1, 0, 1]
    assert result.diagnostics["blocked_actions_total"] == 1
    assert result.diagnostics["blocked_reversals_total"] == 1
    assert result.diagnostics["blocked_long_to_short_reversals"] == 0
    assert result.diagnostics["blocked_short_to_long_reversals"] == 1
    assert result.diagnostics["blocked_entries_total"] == 0


def test_n2b_patch_does_not_block_flat_entries():
    signals = np.array([1, 2, 0, 3], dtype=int)
    regimes = np.array(["NEUTRAL", "BEAR", "BEAR", "BEAR"], dtype=object)

    result = apply_n2b_1d_block_reversals_only(signals, regimes, warmup_bars=3)

    assert result.signals.tolist() == [1, 2, 0, 3]
    assert result.diagnostics["blocked_actions_total"] == 0
    assert result.diagnostics["blocked_entries_total"] == 0


def test_n2b_patch_does_not_change_carried_position_or_close():
    signals = np.array([2, 1, 1, 0], dtype=int)
    regimes = np.array(["NEUTRAL", "BEAR", "BEAR", "BEAR"], dtype=object)

    result = apply_n2b_1d_block_reversals_only(signals, regimes, warmup_bars=3)

    assert result.signals.tolist() == [2, 1, 1, 0]
    assert result.diagnostics["blocked_actions_total"] == 0


def test_n2b_patch_only_applies_to_neutral_to_bear_warmup():
    signals = np.array([2, 1, 3, 2, 3], dtype=int)
    regimes = np.array(["BULL", "BEAR", "BEAR", "BEAR", "BEAR"], dtype=object)

    result = apply_n2b_1d_block_reversals_only(signals, regimes, warmup_bars=3)

    assert result.signals.tolist() == signals.tolist()
    assert result.diagnostics["transition_count"] == 0
    assert result.diagnostics["blocked_actions_total"] == 0


def test_n2b_patch_default_window_is_one_day_of_5m_bars():
    signals = np.ones(BARS_PER_DAY_5M + 3, dtype=int)
    regimes = np.array(["BEAR"] * len(signals), dtype=object)
    signals[0] = 2
    regimes[0] = "NEUTRAL"
    signals[BARS_PER_DAY_5M] = 3
    signals[BARS_PER_DAY_5M + 1] = 3

    result = apply_n2b_1d_block_reversals_only(signals, regimes)

    assert result.signals[BARS_PER_DAY_5M] == 0
    assert result.signals[BARS_PER_DAY_5M + 1] == 3
    assert result.diagnostics["warmup_bars_marked"] == BARS_PER_DAY_5M
    assert result.diagnostics["blocked_actions_total"] == 1


def test_n2b_patch_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        apply_n2b_1d_block_reversals_only(
            np.array([1, 2], dtype=int),
            np.array(["NEUTRAL"], dtype=object),
        )
