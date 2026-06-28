from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0141 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.py"


def load_exp0141():
    spec = importlib.util.spec_from_file_location("exp0141_er_diagnostic", EXP0141)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0141_er_diagnostic"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(
    close: list[float],
    *,
    open_: list[float] | None = None,
    high: list[float] | None = None,
    low: list[float] | None = None,
) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": np.asarray(open_ if open_ is not None else close, dtype=float),
            "high": np.asarray(high if high is not None else values + 1.0, dtype=float),
            "low": np.asarray(low if low is not None else values - 1.0, dtype=float),
            "close": values,
            "volume": np.full(len(values), 100.0),
        }
    )


def test_shifted_efficiency_ratio_excludes_current_bar() -> None:
    exp0141 = load_exp0141()
    df = ohlcv([100.0, 101.0, 99.0, 100.0, 150.0])

    er = exp0141.shifted_efficiency_ratio(df, window=2)

    assert er.iloc[4] == pytest.approx(1.0 / 3.0)


def test_breakout_strength_uses_pre_decision_channel() -> None:
    exp0141 = load_exp0141()
    df = ohlcv(
        [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
        high=[100.0, 101.0, 102.0, 103.0, 999.0, 106.0],
        low=[99.0, 100.0, 101.0, 102.0, 103.0, 104.0],
    )
    trades = [{"entry_step": 5, "step": 5, "type": "sell", "pnl": 1.0}]
    atr = pd.Series([np.nan, np.nan, np.nan, np.nan, 2.0, 2.0])

    strength = exp0141.decision_bar_breakout_strength(df, trades, atr, window=3)

    assert strength.iloc[0] == pytest.approx(0.5)


def test_q1_cross_stats_reports_top_and_worst_shares() -> None:
    exp0141 = load_exp0141()
    trades = pd.DataFrame(
        {
            "pnl": [100.0, 50.0, -30.0, -70.0],
            "trade_return": [0.1, 0.05, -0.03, -0.07],
            "win": [True, True, False, False],
            "is_oos": [False, False, True, True],
            "is_top20_winner": [True, True, False, False],
            "is_worst20_loser": [False, False, True, True],
            "er20_bucket": ["Q1", "Q2", "Q1", "Q2"],
            "er50_bucket": ["Q1", "Q2", "Q1", "Q2"],
            "er100_bucket": ["Q1", "Q2", "Q1", "Q2"],
        }
    )

    bucket_rows = exp0141.er_bucket_summary(trades)
    q1_rows = exp0141.q1_cross_stats(bucket_rows)
    er20_q1 = next(row for row in q1_rows if row["er_window"] == "ER20")

    assert er20_q1["Q1_lowest_ER_top20_count"] == 1
    assert er20_q1["Q1_lowest_ER_top20_pnl_share"] == pytest.approx(100.0 / 150.0)
    assert er20_q1["Q1_lowest_ER_worst20_count"] == 1
    assert er20_q1["Q1_lowest_ER_worst20_loss_share"] == pytest.approx(30.0 / 100.0)


def _bucket_row(
    er_window: str,
    bucket: str,
    *,
    median_return: float = 0.0,
    win_rate: float = 0.5,
    top_count: int = 0,
    top_share: float = 0.0,
    worst_count: int = 0,
    worst_share: float = 0.0,
    pnl_std: float = 1.0,
) -> dict[str, float | int | str]:
    return {
        "er_window": er_window,
        "bucket": bucket,
        "trade_count": 10,
        "median_trade_return": median_return,
        "win_rate": win_rate,
        "top20_winner_count": top_count,
        "top20_winner_pnl_share": top_share,
        "worst20_loser_count": worst_count,
        "worst20_loser_loss_share": worst_share,
        "trade_pnl_std": pnl_std,
    }


def test_stage0_verdict_marks_mixed_high_variance_q1() -> None:
    exp0141 = load_exp0141()
    bucket_rows = []
    rolling_rows = []
    for er_window in ("ER20", "ER50", "ER100"):
        for bucket in ("Q1", "Q2", "Q3", "Q4", "Q5", "missing"):
            if er_window == "ER20" and bucket == "Q1":
                bucket_rows.append(
                    _bucket_row(
                        er_window,
                        bucket,
                        median_return=-0.05,
                        win_rate=0.2,
                        top_count=4,
                        top_share=0.22,
                        worst_count=5,
                        worst_share=0.30,
                        pnl_std=5.0,
                    )
                )
            else:
                bucket_rows.append(_bucket_row(er_window, bucket, median_return=0.01, win_rate=0.5))
            rolling_rows.append(
                {
                    "er_window": er_window,
                    "bucket": bucket,
                    "loss_share_of_window": 0.10,
                }
            )

    rows = exp0141.stage0_verdict_rows(bucket_rows, rolling_rows)
    er20 = next(row for row in rows if row["er_window"] == "ER20")

    assert er20["stage0_verdict"] == "MIXED_HIGH_VARIANCE_BUCKET"
    assert exp0141.overall_stage0_verdict(rows) == "MIXED_HIGH_VARIANCE_BUCKET"


def test_q1_separability_detects_overlap_and_same_dominant_value() -> None:
    exp0141 = load_exp0141()
    rows = [
        {"er_window": "ER20", "extreme_group": "top20", "dimension": "side", "value": "long", "count": 3, "pnl_sum": 10.0},
        {"er_window": "ER20", "extreme_group": "worst20", "dimension": "side", "value": "long", "count": 2, "pnl_sum": -8.0},
    ]

    sep = exp0141.q1_separability_rows(rows)

    assert sep[0]["overlap_value_count"] == 1
    assert sep[0]["same_dominant_value"] is True
    assert sep[0]["separable_hint"] is False
