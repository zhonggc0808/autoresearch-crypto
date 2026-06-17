from __future__ import annotations

import pytest

from scripts.run_risk_overlay_experiments import _damaged_winner_stats


def test_damaged_winner_stats_counts_big_winners_cut_in_half() -> None:
    baseline = [
        {"entry_step": 1, "side": "long", "total_pnl": 10.0},
        {"entry_step": 2, "side": "long", "total_pnl": 20.0},
        {"entry_step": 3, "side": "short", "total_pnl": 30.0},
        {"entry_step": 4, "side": "short", "total_pnl": 40.0},
        {"entry_step": 5, "side": "long", "total_pnl": 100.0},
    ]
    overlay = [
        {"entry_step": 5, "side": "long", "total_pnl": 40.0},
    ]

    stats = _damaged_winner_stats(baseline, overlay)

    assert stats["baseline_big_winner_count"] == 1
    assert stats["damaged_winner_count"] == 1
    assert stats["damaged_winner_share"] == pytest.approx(1.0)
