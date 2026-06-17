from __future__ import annotations

import numpy as np
import pandas as pd

from backtest_quant import (
    build_market_intel_position_sizes,
    buy_hold_signals,
    normalize_signals_for_position_mode,
)


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


def test_build_market_intel_position_sizes_from_csv(tmp_path) -> None:
    valid_df = pd.DataFrame({"datetime": pd.to_datetime(["2025-01-01 10:00", "2025-01-01 14:00"])})
    intel_path = tmp_path / "intel.csv"
    pd.DataFrame(
        {"available_at": ["2025-01-01 09:00", "2025-01-01 13:00"], "mode": ["normal", "defensive"]}
    ).to_csv(intel_path, index=False)

    sizes, stats = build_market_intel_position_sizes(
        valid_df,
        np.array([0, 2]),
        str(intel_path),
        max_age_hours=3,
        unknown_mode="cautious",
    )

    assert sizes.tolist() == [1.0, 0.0]
    assert stats.vetoed_new_entries == 1
    assert stats.mode_counts["normal"] == 1
    assert stats.mode_counts["defensive"] == 1
