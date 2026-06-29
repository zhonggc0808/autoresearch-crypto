import json

import numpy as np
import pytest

from dex.live.timesfm_gate import TimesFMLiveGate, moirai2_quantile_forecast


def test_moirai2_quantile_forecast_extracts_returns():
    quantiles = np.zeros((1, 9, 3), dtype=float)
    quantiles[0, 0, 2] = 90.0
    quantiles[0, 4, 2] = 110.0
    quantiles[0, 8, 2] = 130.0

    forecast = moirai2_quantile_forecast(quantiles, horizon=3, spot=100.0)

    assert forecast["q10_return"] == pytest.approx(-0.1)
    assert forecast["median_return"] == pytest.approx(0.1)
    assert forecast["q90_return"] == pytest.approx(0.3)


def test_live_gate_accepts_moirai2_candidate(tmp_path):
    model_dir = tmp_path / "moirai_model"
    model_dir.mkdir()
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "experiment_id": "exp_test",
                "params": {
                    "strategy_type": "moirai2_quantile_gate",
                    "base_variant": "channel_breakout_v2_2_m375_bbm375_1p5",
                    "model_id": str(model_dir),
                    "context": 1024,
                    "horizon": 72,
                    "min_edge_pct": -0.02,
                    "risk_floor_pct": 0.04,
                },
            }
        ),
        encoding="utf-8",
    )

    gate = TimesFMLiveGate.from_candidate(
        str(candidate),
        "ignored_for_moirai",
        str(tmp_path / "cache.json"),
        checkpoint_variant="channel_breakout_v2_2_m375_bbm375_1p5",
    )

    assert gate.candidate_id == "exp_test"
    assert gate.model_family == "moirai2"
    assert gate.model_source == str(model_dir)
