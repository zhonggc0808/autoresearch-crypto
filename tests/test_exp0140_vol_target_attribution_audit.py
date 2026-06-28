from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0140 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.py"


def load_exp0140():
    spec = importlib.util.spec_from_file_location("exp0140_vol_audit", EXP0140)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0140_vol_audit"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_trade_vol_ref_check_uses_train_only_entry_median() -> None:
    exp0140 = load_exp0140()
    trade_features = pd.DataFrame(
        {
            "entry_time": pd.to_datetime(
                [
                    "2024-01-01",
                    "2024-01-02",
                    "2025-01-01",
                    "2025-01-02",
                ]
            ),
            "realized_vol_20d_at_entry": [0.20, 0.40, 0.80, 1.00],
        }
    )

    out = exp0140.trade_vol_ref_check(trade_features, pd.Timestamp("2024-06-01"))

    assert out["vol_ref_mode"] == "train_only_entry_median"
    assert out["train_only_vol_ref"] == pytest.approx(0.30)
    assert out["full_sample_vol_ref"] == pytest.approx(0.60)
    assert out["vol_ref_ratio_train_over_full"] == pytest.approx(0.50)
    assert out["clean_rerun_required"] is False


def test_exposure_window_stats_reports_active_distribution() -> None:
    exp0140 = load_exp0140()
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=6, freq="1D"),
            "open": np.ones(6),
            "high": np.ones(6),
            "low": np.ones(6),
            "close": np.ones(6),
        }
    )
    exposure = np.asarray([0.0, 1.0, 0.75, 0.50, 0.40, 0.0])

    stats = exp0140.exposure_window_stats(df, exposure, pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05"))

    assert stats["bars"] == 4
    assert stats["active_bars"] == 4
    assert stats["avg_active_size"] == pytest.approx(0.6625)
    assert stats["pct_active_at_100"] == pytest.approx(0.25)
    assert stats["pct_active_lt_75"] == pytest.approx(0.50)
    assert stats["pct_active_at_clip_min"] == pytest.approx(0.25)


def test_paired_extreme_rows_maps_trade_id_and_pnl_saved() -> None:
    exp0140 = load_exp0140()
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=4, freq="5min"),
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
        }
    )
    baseline_trades = [
        {"type": "sell", "entry_step": 1, "step": 2, "pnl": 100.0},
        {"type": "buy_cover", "entry_step": 2, "step": 3, "pnl": -50.0},
    ]
    v1_trades = [
        {"type": "sell", "entry_step": 1, "step": 2, "entry_size": 0.90, "pnl": 90.0},
        {"type": "buy_cover", "entry_step": 2, "step": 3, "entry_size": 0.50, "pnl": -20.0},
    ]
    features = pd.DataFrame(
        {
            "trade_id": [0, 1],
            "entry_time": ["2026-01-01", "2026-01-01"],
            "realized_vol_20d_at_entry": [0.10, 0.50],
            "realized_vol_bucket": ["Q1", "Q5"],
            "is_top20_winner": [True, False],
            "is_worst20_loser": [False, True],
        }
    )

    top = exp0140.paired_extreme_rows(df, baseline_trades, v1_trades, features, kind="top20")
    worst = exp0140.paired_extreme_rows(df, baseline_trades, v1_trades, features, kind="worst20")

    assert top[0]["pnl_lost"] == pytest.approx(10.0)
    assert top[0]["side"] == "long"
    assert worst[0]["pnl_saved"] == pytest.approx(30.0)
    assert worst[0]["side"] == "short"
    assert worst[0]["realized_vol_bucket"] == "Q5"


def test_audit_decision_allows_only_narrow_one_shot_when_residual_dd_is_high_vol_high_size() -> None:
    exp0140 = load_exp0140()
    baseline = {"oos_return": 6.0, "rolling12_min": -0.20}
    v1 = {
        "oos_return": 6.1,
        "rolling12_min": 0.001,
        "dd_improve_vs_baseline": 0.12,
    }
    vol_check = {"clean_rerun_required": False}
    rolling_rows = []
    maxdd_rows = [
        {
            "window": "v1_maxdd_window",
            "realized_vol_pct_median": 0.75,
            "avg_active_size": 0.90,
        }
    ]
    worst20 = {"pnl_saved": 100.0, "avg_v1_size": 0.80}
    top20 = {"damage_or_improve_ratio": 0.10}

    decision = exp0140.audit_decision(baseline, v1, vol_check, rolling_rows, maxdd_rows, worst20, top20)

    assert decision["verdict"] == "ONE_SHOT_REFINEMENT_ALLOWED"
    assert decision["sizing_shadow"] is False
    assert decision["live_action"] == "no_change"
