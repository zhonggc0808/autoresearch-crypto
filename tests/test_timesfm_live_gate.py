import pandas as pd

from dex.live.timesfm_gate import (
    TimesFMLiveGate,
    blocked_signal,
    forecast_allows,
    signal_target,
)


def test_timesfm_gate_decision_helpers() -> None:
    forecast = {"median_return": 0.02, "q10_return": -0.01, "q90_return": 0.03}

    assert signal_target(2, 0) == 1
    assert signal_target(3, 1) == -1
    assert signal_target(1, -1) == -1
    assert blocked_signal(0) == 1
    assert blocked_signal(1) == 0
    assert forecast_allows(1, forecast, -0.01, 0.05)
    assert not forecast_allows(-1, forecast, -0.01, 0.05)


def test_timesfm_live_gate_apply_blocks_entries() -> None:
    gate = TimesFMLiveGate(
        candidate_id="exp_test",
        base_variant="channel_breakout_v2_2_m375_bbm375_1p5",
        model_id="test",
        model_source="test",
        context=32,
        horizon=2,
        min_edge_pct=-0.01,
        risk_floor_pct=0.05,
        cache_path="unused",
    )
    gate._forecast_latest = lambda _df: {  # type: ignore[method-assign]
        "median_return": 0.02,
        "q10_return": -0.01,
        "q90_return": 0.03,
    }
    df = pd.DataFrame({"close": [100.0] * 40})

    signal, info = gate.apply(2, df, current_position=0)
    assert signal == 2
    assert info["allowed"]

    signal, info = gate.apply(3, df, current_position=0)
    assert signal == 1
    assert not info["allowed"]
