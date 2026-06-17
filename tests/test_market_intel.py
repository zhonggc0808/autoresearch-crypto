from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dex.market_intel import build_market_intel_overlay, build_random_entry_control


def test_asof_uses_available_at_at_or_before_decision() -> None:
    bars = pd.DataFrame({"datetime": pd.to_datetime(["2025-01-01 10:00"])})
    intel = pd.DataFrame(
        {
            "available_at": pd.to_datetime(["2025-01-01 10:00", "2025-01-01 10:05"]),
            "mode": ["normal", "defensive"],
        }
    )

    modes, sizes, _ = build_market_intel_overlay(bars, intel)

    assert modes.tolist() == ["normal"]
    assert sizes.tolist() == [1.0]


def test_available_at_wins_over_compatible_time_columns() -> None:
    bars = pd.DataFrame({"datetime": pd.to_datetime(["2025-01-01 10:05"])})
    intel = pd.DataFrame(
        {
            "available_at": pd.to_datetime(["2025-01-01 10:00"]),
            "timestamp": pd.to_datetime(["2025-01-01 10:10"]),
            "mode": ["normal"],
        }
    )

    modes, _, _ = build_market_intel_overlay(bars, intel)

    assert modes.tolist() == ["normal"]


def test_missing_expired_and_entry_stats_are_separate() -> None:
    bars = pd.DataFrame(
        {"datetime": pd.to_datetime(["2025-01-01 08:00", "2025-01-01 10:00", "2025-01-01 15:00"])}
    )
    intel = pd.DataFrame({"available_at": pd.to_datetime(["2025-01-01 09:00"]), "mode": ["normal"]})

    modes, sizes, stats = build_market_intel_overlay(
        bars,
        intel,
        signals=np.array([2, 0, 2]),
        max_age_hours=2,
        unknown_mode="cautious",
    )

    assert modes.tolist() == ["cautious", "normal", "cautious"]
    assert sizes.tolist() == [0.5, 1.0, 0.5]
    assert stats.missing_intel_bars == 1
    assert stats.expired_intel_bars == 1
    assert stats.half_size_entries == 2
    assert stats.vetoed_new_entries == 0


def test_invalid_market_intel_inputs_fail_fast() -> None:
    bars = pd.DataFrame({"datetime": pd.to_datetime(["2025-01-01"])})

    with pytest.raises(ValueError, match="mode"):
        build_market_intel_overlay(bars, pd.DataFrame({"available_at": ["2025-01-01"]}))

    with pytest.raises(ValueError, match="unknown market intel mode"):
        build_market_intel_overlay(
            bars,
            pd.DataFrame({"available_at": ["2025-01-01"], "mode": ["panic"]}),
        )

    with pytest.raises(ValueError, match="contain one of"):
        build_market_intel_overlay(
            pd.DataFrame({"close": [1.0]}),
            pd.DataFrame({"available_at": ["2025-01-01"], "mode": ["normal"]}),
        )


def test_random_entry_control_matches_counts_and_seed() -> None:
    signals = np.array([2, 0, 3, 0, 2, 0, 3])

    sizes_a, stats_a = build_random_entry_control(signals, veto_count=1, half_count=2, seed=7)
    sizes_b, stats_b = build_random_entry_control(signals, veto_count=1, half_count=2, seed=7)

    assert sizes_a.tolist() == sizes_b.tolist()
    assert stats_a.vetoed_new_entries == 1
    assert stats_a.half_size_entries == 2
    assert stats_a.vetoed_entry_steps == stats_b.vetoed_entry_steps
    assert stats_a.half_size_entry_steps == stats_b.half_size_entry_steps
    assert int((sizes_a == 0.0).sum()) == 1
    assert int((sizes_a == 0.5).sum()) == 2


def test_random_entry_control_rejects_too_many_controls() -> None:
    with pytest.raises(ValueError, match="exceed"):
        build_random_entry_control(np.array([2, 0]), veto_count=2, half_count=0)
