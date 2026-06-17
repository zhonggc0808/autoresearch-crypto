from __future__ import annotations

import pandas as pd

from scripts.validate_market_intel_hypotheses import validate_hypotheses


def test_validate_hypotheses_outputs_frozen_candidates() -> None:
    df = pd.DataFrame(
        {
            "side": ["long", "long", "short", "short"],
            "oi_change_1h": [-0.01, 0.01, 0.01, -0.01],
            "price_return_1h": [0.01, 0.01, -0.01, -0.01],
            "funding_zscore": [0.0, 2.5, -2.5, 0.0],
            "pnl": [-10.0, 5.0, -4.0, 3.0],
            "holding_bars": [1, 1, 1, 1],
        }
    )

    results = validate_hypotheses(df, seeds=5)

    assert set(results["candidate"]) == {
        "long_oi_down_price_up",
        "short_oi_up_price_down",
        "abs_funding_z_ge_2",
    }
    assert "random_as_bad_or_worse" in results.columns
